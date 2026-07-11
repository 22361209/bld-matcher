from __future__ import annotations

from flask import Blueprint, jsonify

from app.platform.api_auth import api_scope_required
from app.platform.api_errors import register_api_error_handlers, success_response
from app.platform.api_schemas import OpenApiDocument, PlatformInfoEnvelope
from app.platform.openapi import OpenApiOperation, build_openapi_document, register_openapi_operation


api_v1 = Blueprint("api_v1", __name__)
register_api_error_handlers(api_v1)


@api_v1.get("/api/v1")
@api_scope_required("api:read")
def api_index():
    return success_response(
        {
            "name": "bld-matcher",
            "api_version": "1",
            "capabilities": [
                "principal",
                "scopes",
                "request-id",
                "stable-errors",
                "idempotency",
                "openapi",
                "quotes",
            ],
        }
    )


@api_v1.get("/api/v1/openapi.json")
@api_scope_required("api:read")
def openapi_document():
    return jsonify(build_openapi_document())


register_openapi_operation(
    OpenApiOperation(
        path="/api/v1",
        method="GET",
        operation_id="getApiIndex",
        summary="Read API capabilities",
        scopes=("api:read",),
        response_model=PlatformInfoEnvelope,
    )
)
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/openapi.json",
        method="GET",
        operation_id="getOpenApiDocument",
        summary="Read the OpenAPI document",
        scopes=("api:read",),
        response_model=OpenApiDocument,
    )
)


def register(app) -> None:
    app.register_blueprint(api_v1)
