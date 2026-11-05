"""Authenticated operational pages for preserving the accepted trial ledger."""
from __future__ import annotations

import json

from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request, url_for

from app.security import actor_name, permission_required, wants_json_response

from .domain import CostingConflict, CostingValidationError
from .transfer_preview_service import CostingTransferPreviewService
from .transfer_service import CostingTransferService
from .trial_security import permission_required as trial_permission_required
from .web_support import import_owner, imports, register_error_handlers, service, template_context


def _preview_service() -> CostingTransferPreviewService:
    return CostingTransferPreviewService(
        CostingTransferService(service().db_path), imports().directory, current_app.config["SECRET_KEY"],
    )


def register_transfer_routes(app, *, trial: bool = False) -> None:
    blueprint = Blueprint("costing_transfer", __name__, url_prefix="/costs/transfer")
    protect = trial_permission_required("view_costs") if trial else permission_required("manage_users")

    @blueprint.get("/export")
    @protect
    def export():
        snapshot = CostingTransferService(service().db_path).export_snapshot()
        response = Response(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")), mimetype="application/json")
        response.headers["Content-Disposition"] = 'attachment; filename="costing-trial-transfer.json"'
        response.headers["Cache-Control"] = "no-store"
        return response

    if not trial:
        @blueprint.get("")
        @permission_required("manage_users")
        @permission_required("manage_costs")
        def page():
            return render_template("costing_transfer.html", preview=None, preview_token="", **template_context())

        @blueprint.post("/preview")
        @permission_required("manage_users")
        @permission_required("manage_costs")
        def preview():
            result, token = _preview_service().preview(request.files.get("file"), owner=import_owner())
            if wants_json_response():
                return jsonify(ok=True, preview=result, preview_token=token)
            return render_template("costing_transfer.html", preview=result, preview_token=token, **template_context())

        @blueprint.post("/apply")
        @permission_required("manage_users")
        @permission_required("manage_costs")
        def apply():
            raw = request.get_json(silent=True) if request.is_json else request.form
            token = raw.get("preview_token", "") if raw else ""
            result = _preview_service().apply(token, owner=import_owner(), actor=actor_name())
            if wants_json_response() or request.is_json:
                return jsonify(ok=True, **result)
            flash(f"成本试用资料已接入：{result['records']} 个型号，原修改历史已保留。", "success")
            return redirect(url_for("costing.index"))

        @blueprint.get("/line-codes")
        @permission_required("manage_users")
        @permission_required("manage_costs")
        def line_codes():
            return render_template("costing_line_codes.html", preview=service().preview_line_codes())

        @blueprint.post("/line-codes")
        @permission_required("manage_users")
        @permission_required("manage_costs")
        def apply_line_codes():
            try:
                result = service().apply_line_codes(request.form.get("digest", ""), actor=actor_name())
            except (CostingConflict, CostingValidationError) as error:
                flash(error.message, "error")
                return render_template("costing_line_codes.html", preview=service().preview_line_codes()), (
                    409 if isinstance(error, CostingConflict) else 422
                )
            attributes = result["attributes"]
            flash(
                f"已更新 {result['records']} 个型号，共 {result['items']} 行成本明细。"
                f"已整理纸箱 {result['cartons']} 行，补全产品编码 {result['codes']} 行。"
                f"物料属性已补全外购 {attributes['外购']} 行，自制 {attributes['自制']} 行，"
                f"委外加工 {attributes['委外加工']} 行。", "success",
            )
            return redirect(url_for("costing_transfer.line_codes"), code=303)

    register_error_handlers(blueprint)
    app.register_blueprint(blueprint)
