"""Authenticated browser adapter for atomic development tree edits."""
from __future__ import annotations

import logging

from flask import jsonify, request

from app.security import actor_name, permission_required

from .factory import get_development_service


logger = logging.getLogger(__name__)


def register(app) -> None:
    @app.post("/development/<project_id>/branches/<branch_id>/grid/save")
    @permission_required("edit_development")
    @permission_required("view_development")
    def development_save_grid(project_id, branch_id):
        payload = request.get_json(silent=True)
        if (not isinstance(payload, dict) or set(payload) != {"expected_version", "rows"}
                or type(payload.get("expected_version")) is not int or payload["expected_version"] < 1
                or not isinstance(payload.get("rows"), list)):
            return jsonify(ok=False, code="development.grid_payload", error="批量保存内容或页面版本无效。", field=""), 400
        try:
            result = get_development_service().save_component_grid(
                project_id, branch_id, payload["rows"], expected_version=payload["expected_version"], actor=actor_name(),
            )
        except ValueError as exc:
            code = getattr(exc, "code", "development.grid_invalid")
            status = 409 if "conflict" in code else 404 if code.endswith("not_found") else 400
            return jsonify(ok=False, error=getattr(exc, "message", "请检查所填内容。"),
                           code=code, field=getattr(exc, "field", "")), status
        except Exception:
            logger.exception("Development grid save failed", extra={"project_id": project_id, "branch_id": branch_id})
            return jsonify(ok=False, code="development.grid_failed", error="保存失败，草稿已保留，请稍后重试。", field=""), 500
        response = jsonify(ok=True, version=result["detail"]["project"]["version"],
                           detail=result["detail"], client_ids=result["client_ids"])
        response.headers["Cache-Control"] = "no-store"
        return response
