from __future__ import annotations

import logging

from flask import flash, redirect, request, send_file, url_for

from app.modules.customer_drawings.domain import CustomerDrawingValidationError
from app.modules.customer_drawings.factory import get_customer_drawing_service
from app.security import actor_name, permission_required


logger = logging.getLogger(__name__)


def _drawings_url(customer_id: int) -> str:
    return url_for("customer_detail", customer_id=customer_id, view="drawings")


def _uploads() -> list[object]:
    return [item for item in request.files.getlist("files") if item and item.filename]


def _failed(action: str, customer_id: int, exc: Exception) -> None:
    if isinstance(exc, CustomerDrawingValidationError):
        flash(f"{action}失败：{exc.message}", "error")
        return
    logger.exception("Customer drawing operation failed", extra={"action": action, "customer_id": customer_id})
    flash(f"{action}失败，请稍后重试。", "error")


def register(app) -> None:
    @app.post("/customers/<int:customer_id>/drawings")
    @permission_required("edit_customers")
    def create_customer_drawing(customer_id: int):
        try:
            get_customer_drawing_service().create(
                customer_id,
                request.form,
                files=_uploads(),
                actor=actor_name(),
            )
        except Exception as exc:
            _failed("新增客户图纸", customer_id, exc)
        else:
            flash("客户图纸已保存。", "success")
        return redirect(_drawings_url(customer_id))

    @app.post("/customers/<int:customer_id>/drawings/<int:group_id>/update")
    @permission_required("edit_customers")
    def update_customer_drawing(customer_id: int, group_id: int):
        try:
            get_customer_drawing_service().update(
                customer_id,
                group_id,
                request.form,
                actor=actor_name(),
            )
        except Exception as exc:
            _failed("更新客户图纸", customer_id, exc)
        else:
            flash("客户图纸信息已更新。", "success")
        return redirect(_drawings_url(customer_id))

    @app.post("/customers/<int:customer_id>/drawings/<int:group_id>/versions")
    @permission_required("edit_customers")
    def add_customer_drawing_version(customer_id: int, group_id: int):
        try:
            get_customer_drawing_service().add_version(
                customer_id,
                group_id,
                _uploads(),
                revision_label=request.form.get("revision_label", ""),
                note=request.form.get("note", ""),
                actor=actor_name(),
            )
        except Exception as exc:
            _failed("上传客户图纸新版本", customer_id, exc)
        else:
            flash("客户图纸新版本已保存，旧版本继续保留。", "success")
        return redirect(_drawings_url(customer_id))

    @app.post("/customers/<int:customer_id>/drawings/<int:group_id>/archive")
    @permission_required("delete_customers")
    def archive_customer_drawing(customer_id: int, group_id: int):
        try:
            get_customer_drawing_service().archive(customer_id, group_id, actor=actor_name())
        except Exception as exc:
            _failed("归档客户图纸", customer_id, exc)
        else:
            flash("客户图纸已归档，历史版本和文件仍保留。", "success")
        return redirect(_drawings_url(customer_id))

    @app.post("/customers/<int:customer_id>/drawings/<int:group_id>/unarchive")
    @permission_required("delete_customers")
    def unarchive_customer_drawing(customer_id: int, group_id: int):
        try:
            get_customer_drawing_service().unarchive(customer_id, group_id, actor=actor_name())
        except Exception as exc:
            _failed("恢复客户图纸", customer_id, exc)
        else:
            flash("客户图纸已恢复使用。", "success")
        return redirect(_drawings_url(customer_id))

    @app.get("/customers/<int:customer_id>/drawings/files/<int:file_id>/download")
    @permission_required("view_customers")
    def download_customer_drawing_file(customer_id: int, file_id: int):
        try:
            payload = get_customer_drawing_service().file_payload(
                customer_id,
                file_id,
                actor=actor_name(),
                allow_archived=True,
            )
        except Exception as exc:
            _failed("下载客户图纸", customer_id, exc)
            return redirect(_drawings_url(customer_id))
        response = send_file(
            payload.path,
            as_attachment=True,
            download_name=payload.download_name,
            mimetype=payload.content_type,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/customers/<int:customer_id>/drawings/files/<int:file_id>/preview")
    @permission_required("view_customers")
    def preview_customer_drawing_file(customer_id: int, file_id: int):
        try:
            payload = get_customer_drawing_service().file_payload(
                customer_id,
                file_id,
                actor=actor_name(),
                for_preview=True,
                allow_archived=True,
            )
        except Exception as exc:
            _failed("预览客户图纸", customer_id, exc)
            return redirect(_drawings_url(customer_id))
        response = send_file(
            payload.path,
            as_attachment=False,
            download_name=payload.download_name,
            mimetype=payload.content_type,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
