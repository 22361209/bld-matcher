from __future__ import annotations

import argparse
import json
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for

from app.config import DB_PATH
from app.database import connect, log_event
from app.helpers import download_name, safe_upload_name, user_file_label, user_output_dir, user_recent_outputs, user_upload_dir
from app.security import actor_name, permission_required
from tools import shipment_photo_recognition as recognizer


DEFAULT_LIMIT = 10
JOBS_LOCK = threading.Lock()
JOB_RETENTION_DAYS = 1


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _current_job_owner() -> str:
    return f"{user_output_dir(create=False).as_posix()}::{actor_name()}"


def _job_snapshot(job_id: str) -> dict | None:
    with connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT owner, payload FROM shipment_recognition_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if not row or row["owner"] != _current_job_owner():
        return None
    try:
        snapshot = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None
    if isinstance(snapshot, dict):
        snapshot.pop("owner", None)
        return snapshot
    return None


def _create_job(job_id: str, owner: str, payload: dict) -> None:
    now = _now_text()
    stored = dict(payload)
    stored["updated_at"] = now
    with JOBS_LOCK, connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO shipment_recognition_jobs (id, owner, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, owner, json.dumps(stored, ensure_ascii=False), stored.get("created_at") or now, now),
        )
        conn.commit()


def _cleanup_old_jobs() -> None:
    with connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM shipment_recognition_jobs WHERE updated_at < datetime('now', ?)",
            (f"-{JOB_RETENTION_DAYS} days",),
        )
        conn.commit()


def _update_job(job_id: str, **updates) -> None:
    with JOBS_LOCK, connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT payload FROM shipment_recognition_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return
        try:
            job = json.loads(row["payload"])
        except json.JSONDecodeError:
            job = {"id": job_id}
        job.update(updates)
        job["updated_at"] = _now_text()
        conn.execute(
            "UPDATE shipment_recognition_jobs SET payload = ?, updated_at = ? WHERE id = ?",
            (json.dumps(job, ensure_ascii=False), job["updated_at"], job_id),
        )
        conn.commit()


def _latest_output_rows() -> list[dict[str, str]]:
    rows = []
    for path in user_recent_outputs("货物识别/*.xlsx", limit=20):
        rows.append(
            {
                "path": path,
                "name": path.name,
                "download_name": download_name(path),
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return rows


def _form_limit() -> int:
    raw = request.form.get("limit", "").strip()
    if not raw:
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_LIMIT
    return max(0, min(value, 200))


def _recognizer_args() -> argparse.Namespace:
    provider = request.form.get("provider", "openai-compatible")
    if provider not in {"openai-compatible", "tesseract"}:
        provider = "openai-compatible"
    return argparse.Namespace(
        provider=provider,
        model=request.form.get("model", "").strip() or None,
        base_url=request.form.get("base_url", "").strip() or None,
        endpoint_path="",
        timeout=180,
        max_side=2200,
        limit=_form_limit(),
    )


def _safe_relative_upload_path(filename: str) -> Path:
    parts = []
    for part in Path(filename or "").parts:
        if part in {"", ".", ".."}:
            continue
        safe = safe_upload_name(part)
        if safe:
            parts.append(safe)
    if not parts:
        parts = [f"photo-{datetime.now().strftime('%H%M%S')}"]
    return Path(*parts)


def _save_uploaded_photos() -> tuple[Path, int]:
    files = [file for file in request.files.getlist("shipment_photos") if file and file.filename]
    photos = [file for file in files if Path(file.filename).suffix.lower() in recognizer.IMAGE_SUFFIXES]
    if not photos:
        raise ValueError("请选择 jpg、png、webp、bmp、tif、heic 或 heif 照片。")

    batch_dir = user_upload_dir() / "shipment_photos" / datetime.now().strftime("%Y%m%d-%H%M%S")
    for index, file in enumerate(photos, start=1):
        relative_path = _safe_relative_upload_path(file.filename)
        if not relative_path.suffix:
            relative_path = relative_path.with_suffix(Path(file.filename).suffix.lower())
        destination = (batch_dir / relative_path).resolve()
        if batch_dir.resolve() not in destination.parents:
            destination = batch_dir / f"photo-{index}{Path(file.filename).suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination = destination.with_name(f"{destination.stem}-{index}{destination.suffix}")
        file.save(destination)
    return batch_dir, len(photos)


def _write_recognition_outputs(
    *,
    results: list[dict],
    run_date: str,
    output_dir: Path,
    safe_label: str,
) -> tuple[Path, Path, dict[str, int | float]]:
    output_dir = output_dir / "货物识别"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    excel_path = output_dir / f"shipment-photo-{timestamp}-{safe_label}.xlsx"
    json_path = excel_path.with_suffix(".json")
    recognizer.write_workbook(results, excel_path, run_date)
    json_path.write_text(recognizer.json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = {
        "photos": len(results),
        "labels": sum(len(item["result"].get("labels", [])) for item in results),
        "failed": sum(1 for item in results if item["status"] != "ok"),
        "seconds": round(sum(float(item.get("seconds") or 0) for item in results), 2),
        "prompt_tokens": sum(int(item.get("usage", {}).get("prompt_tokens") or 0) for item in results),
        "completion_tokens": sum(int(item.get("usage", {}).get("completion_tokens") or 0) for item in results),
        "total_tokens": sum(int(item.get("usage", {}).get("total_tokens") or 0) for item in results),
    }
    return excel_path, json_path, stats


def _recognize_jobs(input_dir: Path, args: argparse.Namespace, progress=None) -> list[dict]:
    photos = recognizer.find_photos(input_dir)
    if args.limit > 0:
        photos = photos[: args.limit]
    if not photos:
        raise ValueError("这个文件夹里没有找到 jpg、png、webp、bmp、tif、heic 或 heif 图片。")

    results = []
    total = len(photos)
    for index, photo in enumerate(photos, start=1):
        if progress:
            progress(index=index, total=total, current=photo.relative_name, completed=index - 1, phase="recognizing")
        results.append(recognizer.recognize_photo(photo, args))
        if progress:
            progress(index=index, total=total, current=photo.relative_name, completed=index, phase="recognized", result=results[-1])
    return results


def _run_recognition(input_dir: Path, args: argparse.Namespace, run_date: str) -> tuple[Path, Path, dict[str, int | float]]:
    results = _recognize_jobs(input_dir, args)
    excel_path, json_path, stats = _write_recognition_outputs(
        results=results,
        run_date=run_date,
        output_dir=user_output_dir(),
        safe_label=user_file_label(),
    )
    return excel_path, json_path, stats


def _run_recognition_job(
    *,
    app,
    job_id: str,
    input_dir: Path,
    args: argparse.Namespace,
    run_date: str,
    output_dir: Path,
    safe_label: str,
    uploaded_count: int,
    actor: str,
) -> None:
    started = datetime.now()

    def progress(**state) -> None:
        total = int(state.get("total") or 0)
        completed = int(state.get("completed") or 0)
        percent = int((completed / total) * 100) if total else 0
        current = state.get("current") or ""
        phase = state.get("phase") or ""
        result = state.get("result") or {}
        _update_job(
            job_id,
            status="running",
            total=total,
            completed=completed,
            percent=percent,
            current=current,
            phase=phase,
            message=f"正在识别 {completed + 1}/{total}：{current}" if phase == "recognizing" else f"已完成 {completed}/{total}",
            last_seconds=result.get("seconds", 0) if isinstance(result, dict) else 0,
        )

    with app.app_context():
        try:
            _update_job(job_id, status="running", message="开始调用视觉模型", started_at=_now_text())
            results = _recognize_jobs(input_dir, args, progress=progress)
            _update_job(job_id, phase="writing", message="正在生成 Excel 和 JSON")
            excel_path, json_path, stats = _write_recognition_outputs(
                results=results,
                run_date=run_date,
                output_dir=output_dir,
                safe_label=safe_label,
            )
            with connect(DB_PATH) as conn:
                log_event(
                    conn,
                    "生成货物识别汇总",
                    "shipment_recognition",
                    excel_path.name,
                    f"上传 {uploaded_count} 张，识别照片 {stats['photos']} 张，标签 {stats['labels']} 张，失败 {stats['failed']} 张",
                    actor=actor,
                )
                conn.commit()
            elapsed = round((datetime.now() - started).total_seconds(), 2)
            _update_job(
                job_id,
                status="completed",
                phase="completed",
                completed=stats["photos"],
                total=stats["photos"],
                percent=100,
                message="货物识别已完成",
                elapsed_seconds=elapsed,
                result={
                    "excel_name": download_name(excel_path),
                    "json_name": download_name(json_path),
                    "excel_filename": excel_path.name,
                    "json_filename": json_path.name,
                    **stats,
                },
            )
        except Exception as exc:
            _update_job(job_id, status="error", phase="error", message="识别失败", error=str(exc))


def register(app) -> None:
    @app.get("/shipment-recognition")
    @permission_required("recognize_shipments")
    def shipment_recognition():
        return render_template(
            "shipment_recognition.html",
            active_page="shipment_recognition",
            default_date=date.today().isoformat(),
            default_limit=DEFAULT_LIMIT,
            latest_outputs=_latest_output_rows(),
            result=None,
        )

    @app.post("/shipment-recognition/run")
    @permission_required("recognize_shipments")
    def run_shipment_recognition():
        run_date = request.form.get("shipment_date", "").strip() or date.today().isoformat()
        args = _recognizer_args()
        wants_json = request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"
        try:
            batch_dir, uploaded_count = _save_uploaded_photos()
            if wants_json:
                _cleanup_old_jobs()
                job_id = uuid.uuid4().hex
                owner = _current_job_owner()
                _create_job(
                    job_id,
                    owner,
                    {
                        "id": job_id,
                        "owner": owner,
                        "status": "queued",
                        "phase": "queued",
                        "message": f"已上传 {uploaded_count} 张照片，等待开始识别",
                        "total": uploaded_count,
                        "completed": 0,
                        "percent": 0,
                        "current": "",
                        "total_tokens": 0,
                        "created_at": _now_text(),
                    },
                )
                thread = threading.Thread(
                    target=_run_recognition_job,
                    kwargs={
                        "app": current_app._get_current_object(),
                        "job_id": job_id,
                        "input_dir": batch_dir,
                        "args": args,
                        "run_date": run_date,
                        "output_dir": user_output_dir(),
                        "safe_label": user_file_label(),
                        "uploaded_count": uploaded_count,
                        "actor": actor_name(),
                    },
                    daemon=True,
                )
                thread.start()
                return jsonify({"ok": True, "job_id": job_id, "status_url": url_for("shipment_recognition_status", job_id=job_id)}), 202

            excel_path, json_path, stats = _run_recognition(batch_dir, args, run_date)
            with connect(DB_PATH) as conn:
                log_event(
                    conn,
                    "生成货物识别汇总",
                    "shipment_recognition",
                    excel_path.name,
                    f"上传 {uploaded_count} 张，识别照片 {stats['photos']} 张，标签 {stats['labels']} 张，失败 {stats['failed']} 张",
                    actor=actor_name(),
                )
                conn.commit()
        except Exception as exc:
            if wants_json:
                return jsonify({"ok": False, "error": str(exc)}), 400
            flash(f"识别失败：{exc}", "error")
            return redirect(url_for("shipment_recognition"))

        flash("货物识别已完成。", "success")
        return render_template(
            "shipment_recognition.html",
            active_page="shipment_recognition",
            default_date=run_date,
            default_limit=args.limit,
            latest_outputs=_latest_output_rows(),
            result={
                "excel_path": excel_path,
                "json_path": json_path,
                "excel_name": download_name(excel_path),
                "json_name": download_name(json_path),
                **stats,
            },
        )

    @app.get("/shipment-recognition/status/<job_id>")
    @permission_required("recognize_shipments")
    def shipment_recognition_status(job_id: str):
        snapshot = _job_snapshot(job_id)
        if not snapshot:
            return jsonify({"ok": False, "error": "任务不存在或已失效。"}), 404
        result = snapshot.get("result")
        if isinstance(result, dict):
            result = dict(result)
            result["excel_url"] = url_for("download", name=result["excel_name"]) if result.get("excel_name") else ""
            result["json_url"] = url_for("download", name=result["json_name"]) if result.get("json_name") else ""
            snapshot["result"] = result
        return jsonify({"ok": True, "job": snapshot})
