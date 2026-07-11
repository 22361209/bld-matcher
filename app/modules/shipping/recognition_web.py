from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from pathlib import Path

from flask import flash, g, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from app.helpers import download_name, safe_upload_name, user_file_label, user_output_dir, user_recent_outputs, user_upload_dir
from app.platform.jobs.domain import JobNotFoundError, JobRecord
from app.security import actor_name, permission_required, wants_json_response
from tools import shipment_photo_recognition as recognizer

from .factory import get_shipping_recognition_service
from .recognition_service import DEFAULT_LIMIT


logger = logging.getLogger(__name__)


def _owner_id() -> str:
    user = getattr(g, "user", None)
    if not user:
        return "ui:anonymous"
    return f"ui:user:{int(user['id'])}"


def _legacy_owner_id() -> str:
    return f"{user_output_dir(create=False).as_posix()}::{actor_name()}"


def _job(job_id: str) -> JobRecord | None:
    service = get_shipping_recognition_service()
    for owner_id in (_owner_id(), _legacy_owner_id()):
        try:
            return service.job_service.get(job_id, owner_id=owner_id)
        except JobNotFoundError:
            continue
    return None


def _latest_output_rows() -> list[dict[str, str]]:
    return [
        {
            "name": path.name,
            "download_name": download_name(path),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
        for path in user_recent_outputs("货物识别/*.xlsx", limit=20)
    ]


def _form_limit() -> int:
    try:
        return max(0, min(int(request.form.get("limit", DEFAULT_LIMIT)), 200))
    except ValueError:
        return DEFAULT_LIMIT


def _safe_relative_upload_path(filename: str) -> Path:
    parts = [safe_upload_name(part) for part in Path(filename or "").parts if part not in {"", ".", ".."}]
    safe_parts = [part for part in parts if part]
    return Path(*safe_parts) if safe_parts else Path(f"photo-{uuid.uuid4().hex[:8]}")


def _save_uploaded_photos() -> tuple[Path, int]:
    files = [file for file in request.files.getlist("shipment_photos") if file and file.filename]
    photos = [file for file in files if Path(file.filename or "").suffix.lower() in recognizer.IMAGE_SUFFIXES]
    if not photos:
        raise ValueError("请选择 jpg、png、webp、bmp、tif、heic 或 heif 照片。")
    batch_dir = user_upload_dir() / "shipment_photos" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    batch_root = batch_dir.resolve()
    for index, file in enumerate(photos, start=1):
        filename = file.filename or ""
        relative = _safe_relative_upload_path(filename)
        if not relative.suffix:
            relative = relative.with_suffix(Path(filename).suffix.lower())
        destination = (batch_dir / relative).resolve()
        if batch_root not in destination.parents:
            destination = batch_dir / f"photo-{index}{Path(filename).suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination = destination.with_name(f"{destination.stem}-{index}{destination.suffix}")
        file.save(destination)
    return batch_dir, len(photos)


def _web_job_payload(job: JobRecord) -> dict[str, object]:
    payload = job.public_payload()
    progress = payload.pop("progress", {})
    if isinstance(progress, dict):
        payload.update(progress)
    result = payload.get("result")
    if isinstance(result, dict):
        result = dict(result)
        result["excel_url"] = url_for("download", name=result["excel_name"]) if result.get("excel_name") else ""
        result["json_url"] = url_for("download", name=result["json_name"]) if result.get("json_name") else ""
        payload["result"] = result
    error = payload.get("error")
    if isinstance(error, dict):
        payload["error"] = str(error.get("message") or "任务执行失败。")
        payload["error_code"] = str(error.get("code") or "job.failed")
    return payload


def register(app) -> None:
    @app.get("/shipment-recognition")
    @permission_required("recognize_shipments")
    def shipment_recognition():
        active_job = _job(request.args.get("job_id", "")) if request.args.get("job_id") else None
        return render_template(
            "shipment_recognition.html",
            active_page="shipment_recognition",
            default_date=date.today().isoformat(),
            default_limit=DEFAULT_LIMIT,
            latest_outputs=_latest_output_rows(),
            result=None,
            active_job=active_job,
        )

    @app.post("/shipment-recognition/run")
    @permission_required("recognize_shipments")
    def run_shipment_recognition():
        try:
            batch_dir, uploaded_count = _save_uploaded_photos()
            job = get_shipping_recognition_service().submit(
                input_dir=batch_dir,
                output_dir=user_output_dir(),
                run_date=request.form.get("shipment_date", "").strip() or date.today().isoformat(),
                safe_label=user_file_label(),
                uploaded_count=uploaded_count,
                limit=_form_limit(),
                owner_id=_owner_id(),
                actor=actor_name(),
            )
        except ValueError as exc:
            if wants_json_response():
                return jsonify({"ok": False, "error": str(exc)}), 400
            flash(str(exc), "error")
            return redirect(url_for("shipment_recognition"))
        except RequestEntityTooLarge:
            raise
        except Exception:
            logger.exception("Shipment recognition job submission failed")
            if wants_json_response():
                return jsonify({"ok": False, "error": "识别任务提交失败，请稍后重试。"}), 500
            flash("识别任务提交失败，请稍后重试。", "error")
            return redirect(url_for("shipment_recognition"))
        status_url = url_for("shipment_recognition_status", job_id=job.id)
        cancel_url = url_for("cancel_shipment_recognition", job_id=job.id)
        if wants_json_response():
            return jsonify({"ok": True, "job_id": job.id, "status_url": status_url, "cancel_url": cancel_url}), 202
        flash("照片已上传，识别任务进入队列。", "success")
        return redirect(url_for("shipment_recognition", job_id=job.id))

    @app.get("/shipment-recognition/status/<job_id>")
    @permission_required("recognize_shipments")
    def shipment_recognition_status(job_id: str):
        job = _job(job_id)
        if job is None:
            return jsonify({"ok": False, "error": "任务不存在或已失效。"}), 404
        return jsonify({"ok": True, "job": _web_job_payload(job)})

    @app.post("/shipment-recognition/jobs/<job_id>/cancel")
    @permission_required("recognize_shipments")
    def cancel_shipment_recognition(job_id: str):
        try:
            job = get_shipping_recognition_service().job_service.cancel(
                job_id,
                owner_id=_owner_id(),
                reason="ui_request",
            )
        except JobNotFoundError:
            return jsonify({"ok": False, "error": "任务不存在或已失效。"}), 404
        return jsonify({"ok": True, "job": _web_job_payload(job)})
