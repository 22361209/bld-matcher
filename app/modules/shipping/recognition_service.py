from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.platform.ai import AiProviderInterruptedError
from app.platform.jobs.domain import JobRecord
from app.platform.jobs.service import JobService
from tools import shipment_photo_recognition as recognizer


logger = logging.getLogger(__name__)
RECOGNITION_JOB_KIND = "shipping.recognition"
DEFAULT_LIMIT = 10


class ShipmentRecognitionService:
    def __init__(
        self,
        job_service: JobService,
        unit_of_work_factory,
        *,
        upload_root: Path,
        output_root: Path,
        job_ttl: timedelta = timedelta(days=7),
    ) -> None:
        self.job_service = job_service
        self.unit_of_work_factory = unit_of_work_factory
        self.upload_root = upload_root.resolve()
        self.output_root = output_root.resolve()
        self.job_ttl = job_ttl

    def submit(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        run_date: str,
        safe_label: str,
        uploaded_count: int,
        limit: int,
        owner_id: str,
        actor: str,
    ) -> JobRecord:
        checked_input = self._checked_directory(input_dir, self.upload_root, "上传目录")
        checked_output = self._checked_directory(output_dir, self.output_root, "输出目录", create=True)
        try:
            normalized_date = date.fromisoformat(run_date).isoformat()
        except ValueError as exc:
            raise ValueError("发货日期格式无效。") from exc
        label = re.sub(r"[^A-Za-z0-9_-]+", "-", safe_label).strip("-") or "user"
        total = max(1, int(uploaded_count))
        return self.job_service.submit(
            kind=RECOGNITION_JOB_KIND,
            owner_id=owner_id,
            request_payload={
                "input_dir": str(checked_input),
                "output_dir": str(checked_output),
                "run_date": normalized_date,
                "safe_label": label,
                "uploaded_count": total,
                "limit": max(0, min(int(limit), 200)),
                "actor": actor,
                "protected_paths": [str(checked_input)],
            },
            progress={
                "phase": "queued",
                "message": f"已上传 {total} 张照片，等待识别 Worker",
                "total": total,
                "completed": 0,
                "percent": 0,
                "current": "",
            },
            max_attempts=3,
            ttl=self.job_ttl,
        )

    def execute(
        self,
        *,
        job_id: str,
        payload: dict[str, Any],
        update_progress: Callable[[dict[str, Any]], None],
        check_cancelled: Callable[[], None],
    ) -> dict[str, Any]:
        input_dir = self._checked_directory(Path(str(payload.get("input_dir") or "")), self.upload_root, "上传目录")
        output_dir = self._checked_directory(
            Path(str(payload.get("output_dir") or "")),
            self.output_root,
            "输出目录",
            create=True,
        )
        try:
            run_date = date.fromisoformat(str(payload.get("run_date") or "")).isoformat()
        except ValueError as exc:
            raise ValueError("任务中的发货日期无效。") from exc
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "-", str(payload.get("safe_label") or "user")).strip("-") or "user"
        actor = str(payload.get("actor") or "worker")[:100]
        uploaded_count = max(1, int(payload.get("uploaded_count") or 1))
        limit = max(0, min(int(payload.get("limit") or 0), 200))
        try:
            args = recognizer.build_runtime_args(limit=limit, caller=actor or "shipment-recognition-worker")
        except Exception as exc:
            logger.exception("Shipment vision provider configuration is invalid")
            raise ValueError("视觉识别服务尚未正确配置。") from exc
        args.check_interrupted = check_cancelled

        photos = recognizer.find_photos(input_dir)
        if args.limit > 0:
            photos = photos[: args.limit]
        if not photos:
            raise ValueError("上传目录里没有可识别的照片。")

        results: list[dict[str, Any]] = []
        total = len(photos)
        for index, photo in enumerate(photos, start=1):
            check_cancelled()
            update_progress(
                {
                    "phase": "recognizing",
                    "message": f"正在识别 {index}/{total}：{photo.relative_name}",
                    "total": total,
                    "completed": index - 1,
                    "percent": int(((index - 1) / total) * 100),
                    "current": photo.relative_name,
                }
            )
            try:
                result = recognizer.recognize_photo(photo, args)
            except AiProviderInterruptedError as exc:
                try:
                    with self.unit_of_work_factory() as unit_of_work:
                        unit_of_work.repository.record_ai_call(job_id=job_id, metrics=exc.metrics.audit_payload())
                        unit_of_work.commit()
                finally:
                    check_cancelled()
                raise
            results.append(result)
            metrics = result.get("ai_metrics")
            if isinstance(metrics, dict) and metrics:
                with self.unit_of_work_factory() as unit_of_work:
                    unit_of_work.repository.record_ai_call(job_id=job_id, metrics=metrics)
                    unit_of_work.commit()
            update_progress(
                {
                    "phase": "recognized",
                    "message": f"已完成 {index}/{total}",
                    "total": total,
                    "completed": index,
                    "percent": int((index / total) * 95),
                    "current": photo.relative_name,
                }
            )

        check_cancelled()
        update_progress(
            {
                "phase": "writing",
                "message": "正在生成 Excel 和 JSON",
                "total": total,
                "completed": total,
                "percent": 97,
                "current": "",
            }
        )
        excel_path, json_path, stats = self._write_outputs(
            results=results,
            run_date=run_date,
            output_dir=output_dir,
            safe_label=safe_label,
        )
        try:
            check_cancelled()
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.repository.audit_recognition(
                    "生成货物识别汇总",
                    excel_path.name,
                    f"上传 {uploaded_count} 张，识别照片 {stats['photos']} 张，标签 {stats['labels']} 张，失败 {stats['failed']} 张",
                    actor=actor,
                )
                unit_of_work.commit()
        except Exception:
            excel_path.unlink(missing_ok=True)
            json_path.unlink(missing_ok=True)
            raise
        return {
            "excel_name": excel_path.name,
            "json_name": json_path.name,
            "excel_filename": excel_path.name,
            "json_filename": json_path.name,
            **stats,
        }

    @staticmethod
    def _write_outputs(
        *,
        results: list[dict[str, Any]],
        run_date: str,
        output_dir: Path,
        safe_label: str,
    ) -> tuple[Path, Path, dict[str, int | float]]:
        destination = output_dir / "货物识别"
        destination.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        excel_path = destination / f"shipment-photo-{timestamp}-{safe_label}.xlsx"
        if excel_path.exists():
            excel_path = destination / f"shipment-photo-{timestamp}-{safe_label}-{uuid.uuid4().hex[:6]}.xlsx"
        json_path = excel_path.with_suffix(".json")
        excel_temporary = excel_path.with_name(f".{excel_path.stem}.{uuid.uuid4().hex}.tmp.xlsx")
        json_temporary = json_path.with_name(f".{json_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            recognizer.write_workbook(results, excel_temporary, run_date)
            json_temporary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(excel_temporary, excel_path)
            os.replace(json_temporary, json_path)
        except Exception:
            excel_path.unlink(missing_ok=True)
            json_path.unlink(missing_ok=True)
            raise
        finally:
            excel_temporary.unlink(missing_ok=True)
            json_temporary.unlink(missing_ok=True)
        stats: dict[str, int | float] = {
            "photos": len(results),
            "labels": sum(len(item["result"].get("labels", [])) for item in results),
            "failed": sum(1 for item in results if item["status"] != "ok"),
            "seconds": round(sum(float(item.get("seconds") or 0) for item in results), 2),
            "prompt_tokens": sum(int(item.get("usage", {}).get("prompt_tokens") or 0) for item in results),
            "completion_tokens": sum(int(item.get("usage", {}).get("completion_tokens") or 0) for item in results),
            "total_tokens": sum(int(item.get("usage", {}).get("total_tokens") or 0) for item in results),
            "estimated_cost_usd": round(
                sum(float(item.get("ai_metrics", {}).get("estimated_cost_usd") or 0) for item in results),
                8,
            ),
        }
        return excel_path, json_path, stats

    @staticmethod
    def _checked_directory(path: Path, root: Path, label: str, *, create: bool = False) -> Path:
        resolved = path.expanduser().resolve()
        if root != resolved and root not in resolved.parents:
            raise ValueError(f"{label}不在允许范围内。")
        if create:
            resolved.mkdir(parents=True, exist_ok=True)
        if not resolved.is_dir():
            raise ValueError(f"{label}不存在。")
        return resolved
