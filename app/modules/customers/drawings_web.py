from __future__ import annotations

import logging

from flask import flash, redirect, send_file, url_for

from app.modules.customer_products.domain import CustomerProductValidationError
from app.modules.customer_products.factory import get_customer_product_service
from app.security import actor_name, permission_required


logger = logging.getLogger(__name__)


def _detail_url(customer_id: int) -> str:
    return url_for("customer_detail", customer_id=customer_id)


def _failed(action: str, customer_id: int, exc: Exception) -> None:
    if isinstance(exc, CustomerProductValidationError):
        flash(f"{action}失败：{exc.message}", "error")
        return
    logger.exception("Customer drawing file operation failed", extra={"action": action, "customer_id": customer_id})
    flash(f"{action}失败，请稍后重试。", "error")


def register(app) -> None:
    @app.get("/customers/<int:customer_id>/drawings/files/<int:file_id>/download")
    @permission_required("view_customers")
    def download_customer_drawing_file(customer_id: int, file_id: int):
        try:
            payload = get_customer_product_service().file_payload(
                customer_id,
                file_id,
                actor=actor_name(),
            )
        except Exception as exc:
            _failed("下载客户图纸", customer_id, exc)
            return redirect(_detail_url(customer_id))
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
            payload = get_customer_product_service().file_payload(
                customer_id,
                file_id,
                actor=actor_name(),
                for_preview=True,
            )
        except Exception as exc:
            _failed("预览客户图纸", customer_id, exc)
            return redirect(_detail_url(customer_id))
        response = send_file(
            payload.path,
            as_attachment=False,
            download_name=payload.download_name,
            mimetype=payload.content_type,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
