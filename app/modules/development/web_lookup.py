from __future__ import annotations

import logging

from flask import jsonify, request, url_for

from app.security import permission_required

from .domain import DevelopmentValidationError
from .lookup_factory import get_development_lookup_service


logger = logging.getLogger(__name__)


def _response(action):
    try:
        result = action()
    except DevelopmentValidationError as exc:
        status = 404 if exc.code.endswith("not_found") else 400
        return jsonify({"ok": False, "code": exc.code, "error": exc.message}), status
    except Exception:
        logger.exception("Development lookup failed")
        return jsonify({"ok": False, "code": "development.lookup_failed", "error": "检索失败，请稍后重试。"}), 500
    response = jsonify({"ok": True, **result})
    response.headers["Cache-Control"] = "private, no-store"
    return response


def register(app) -> None:
    @app.get("/development/<project_id>/lookup/sources")
    @permission_required("view_development")
    def development_lookup_sources(project_id):
        raw_limit = request.args.get("limit", "20")
        limit = int(raw_limit) if raw_limit.isascii() and raw_limit.isdigit() and len(raw_limit) <= 2 else 0
        return _response(lambda: get_development_lookup_service().search_sources(
            project_id, query=request.args.get("q", ""), component_id=request.args.get("component_id", ""), limit=limit,
        ))

    @app.get("/development/<project_id>/lookup/components/<component_id>")
    @permission_required("view_development")
    @permission_required("view_development_files")
    def development_lookup_component_context(project_id, component_id):
        def context():
            result = get_development_lookup_service().component_context(project_id, component_id)
            result["documents_url"] = url_for("development_detail", project_id=project_id, view="documents",
                                              branch=result["component"]["branch_id"], component=component_id)
            for document in result["documents"]:
                document["download_url"] = url_for("development_download_document", project_id=project_id,
                                                   revision_id=document["revision_id"])
                if document["previewable"]:
                    document["preview_url"] = url_for("development_preview_document", project_id=project_id,
                                                      revision_id=document["revision_id"])
            return result
        return _response(context)
