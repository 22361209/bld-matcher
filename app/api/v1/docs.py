from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response

from app.platform.api_auth import api_scope_required
from app.platform.api_errors import ApiError, register_api_error_handlers
from app.platform.openapi import OpenApiOperation, register_openapi_operation


docs_v1_api = Blueprint("docs_v1_api", __name__)
register_api_error_handlers(docs_v1_api)

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "api"


def available_api_docs() -> list[str]:
    return sorted(path.name for path in DOCS_DIR.glob("*.md"))


@docs_v1_api.get("/api/v1/docs/<doc_name>")
@api_scope_required("api:read")
def read_api_doc(doc_name: str):
    candidate = (DOCS_DIR / doc_name).resolve()
    if candidate.suffix != ".md" or candidate.parent != DOCS_DIR or not candidate.is_file():
        raise ApiError(
            "api.doc_not_found",
            "API 文档不存在。",
            404,
            {"available_docs": available_api_docs()},
        )
    return Response(
        candidate.read_text(encoding="utf-8"),
        mimetype="text/markdown; charset=utf-8",
    )


register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/docs/{doc_name}",
        method="GET",
        operation_id="readApiDoc",
        summary="Read an API guide markdown document",
        scopes=("api:read",),
        response_model=None,
        response_media_type="text/markdown",
        response_schema={"type": "string"},
        path_parameters=(("doc_name", "string"),),
    )
)


def register(app) -> None:
    app.register_blueprint(docs_v1_api)
