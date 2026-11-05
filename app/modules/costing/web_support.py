"""Request context and error presentation shared by the formal and trial adapters."""
from __future__ import annotations

import logging
import secrets

from flask import Blueprint, current_app, flash, g, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from app.security import wants_json_response

from .domain import COST_KIND_LABELS, MATERIAL_ATTRIBUTES, CostingConflict, CostingNotFound, CostingValidationError
from .grid import vehicle_parts
from .service import CostingService
from .transfer_service import MAX_TRANSFER_MIB
from .weight_lookup import CostWeightLookup


logger = logging.getLogger(__name__)


def service() -> CostingService:
    return current_app.extensions["costing_service"]


def imports():
    return current_app.extensions["costing_imports"]


def weights() -> CostWeightLookup:
    return current_app.extensions["costing_weights"]


def costing_endpoint() -> str:
    return "costing_trial" if current_app.config.get("COSTING_TRIAL_MODE") else "costing"


def template_context() -> dict:
    return {"costing_endpoint": costing_endpoint(), "costing_material_attributes": MATERIAL_ATTRIBUTES,
            "costing_kind_labels": COST_KIND_LABELS, "costing_vehicle_parts": vehicle_parts,
            "costing_trial_mode": bool(current_app.config.get("COSTING_TRIAL_MODE"))}


def import_owner() -> str:
    if current_app.config.get("COSTING_TRIAL_MODE"):
        return session["costing_owner"]
    user_id = str(g.user["id"])
    state = session.get("costing_import_owner")
    if not isinstance(state, dict) or state.get("user") != user_id or not state.get("nonce"):
        state = {"user": user_id, "nonce": secrets.token_urlsafe(24)}
        session["costing_import_owner"] = state
    return f"{user_id}:{state['nonce']}"


def error_response(message: str, status: int, *, field: str = ""):
    if wants_json_response() or request.is_json:
        payload = {"ok": False, "error": message}
        if field:
            payload["field"] = field
        return jsonify(payload), status
    flash(message, "error")
    if request.blueprint == "costing_import":
        return render_template("costing_import.html", preview=None, preview_token=""), status
    if request.blueprint == "costing_transfer" and not current_app.config.get("COSTING_TRIAL_MODE"):
        return render_template("costing_transfer.html", preview=None, preview_token=""), status
    return render_template("costing_list.html", result={"records": [], "total": 0, "page": 1,
                           "page_size": 50, "pages": 1}, filters={}), status


def register_error_handlers(blueprint: Blueprint) -> None:
    @blueprint.after_request
    def private_response(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @blueprint.errorhandler(CostingValidationError)
    def invalid(error):
        return error_response(error.message, 422, field=error.field)

    @blueprint.errorhandler(CostingConflict)
    def conflict(error):
        return error_response(error.message, 409)

    @blueprint.errorhandler(CostingNotFound)
    def missing(error):
        return error_response(error.message, 404)

    @blueprint.errorhandler(HTTPException)
    def http_error(error):
        too_large = f"成本接入包不能超过 {MAX_TRANSFER_MIB} MiB。" if request.blueprint == "costing_transfer" else "文件过大，请使用 12 MB 以内的工作簿。"
        messages = {400: "请求格式不正确。", 413: too_large, 404: "页面不存在。"}
        return error_response(messages.get(error.code, "请求无法完成。"), error.code or 500)

    @blueprint.errorhandler(Exception)
    def unexpected(error):
        logger.exception("Cost request failed", extra={"endpoint": request.endpoint})
        return error_response("暂时无法完成操作，请保留输入后重试。", 500)
