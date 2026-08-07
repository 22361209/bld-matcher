from __future__ import annotations

import logging

from flask import flash, jsonify, redirect, request, url_for

from app.security import actor_name, permission_required, wants_json_response

from .domain import Customer, CustomerValidationError
from .factory import get_customer_service


logger = logging.getLogger(__name__)


def _overview_url(customer_id: int) -> str:
    return url_for("customer_detail", customer_id=customer_id, view="overview")


def _success(customer: Customer, message: str):
    if wants_json_response():
        return jsonify({"ok": True, "customer": {"id": customer.id, "name": customer.name}})
    flash(message, "success")
    return redirect(_overview_url(customer.id))


def _failure(customer_id: int, prefix: str, error: Exception):
    if wants_json_response():
        message = error.message if isinstance(error, CustomerValidationError) else "客户资料更新失败，请稍后重试。"
        return jsonify({"ok": False, "error": message}), 400 if isinstance(error, CustomerValidationError) else 500
    if isinstance(error, CustomerValidationError):
        flash(f"{prefix}失败：{error.message}", "error")
    else:
        flash(f"{prefix}失败，请稍后重试。", "error")
    return redirect(_overview_url(customer_id))


def register(app) -> None:
    @app.post("/customers/<int:customer_id>/owner")
    @permission_required("edit_customers")
    def update_customer_owner(customer_id: int):
        try:
            customer = get_customer_service().update_owner(
                customer_id,
                request.form.get("owner_username", ""),
                actor=actor_name(),
            )
        except CustomerValidationError as exc:
            return _failure(customer_id, "负责人更新", exc)
        except Exception as exc:
            logger.exception("Customer owner update failed", extra={"customer_id": customer_id})
            return _failure(customer_id, "负责人更新", exc)
        return _success(customer, "负责人已更新。")

    @app.post("/customers/<int:customer_id>/identity/name")
    @permission_required("change_customer_identity")
    def rename_customer(customer_id: int):
        try:
            customer = get_customer_service().rename(
                customer_id,
                request.form.get("name", ""),
                reason=request.form.get("reason", ""),
                actor=actor_name(),
            )
        except CustomerValidationError as exc:
            return _failure(customer_id, "客户名称变更", exc)
        except Exception as exc:
            logger.exception("Customer name change failed", extra={"customer_id": customer_id})
            return _failure(customer_id, "客户名称变更", exc)
        return _success(customer, "客户名称已变更，历史报价已同步。")

    @app.post("/customers/<int:customer_id>/identity/code")
    @permission_required("change_customer_identity")
    def update_customer_code(customer_id: int):
        try:
            customer = get_customer_service().update_code(
                customer_id,
                request.form.get("code", ""),
                reason=request.form.get("reason", ""),
                actor=actor_name(),
            )
        except CustomerValidationError as exc:
            return _failure(customer_id, "客户编号变更", exc)
        except Exception as exc:
            logger.exception("Customer code change failed", extra={"customer_id": customer_id})
            return _failure(customer_id, "客户编号变更", exc)
        return _success(customer, "客户编号已变更。")
