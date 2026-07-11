from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from .api_schemas import ApiErrorEnvelope, ApiSuccessEnvelope


@dataclass(frozen=True, slots=True)
class OpenApiOperation:
    path: str
    method: str
    operation_id: str
    summary: str
    scopes: tuple[str, ...]
    response_model: type[BaseModel] = ApiSuccessEnvelope
    request_model: type[BaseModel] | None = None
    success_status: int = 200


_OPERATIONS: dict[tuple[str, str], OpenApiOperation] = {}


def register_openapi_operation(operation: OpenApiOperation) -> None:
    _OPERATIONS[(operation.path, operation.method.lower())] = operation


def _schema_for(model: type[BaseModel], components: dict[str, dict]) -> dict:
    schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = schema.pop("$defs", {})
    components.update(definitions)
    components[model.__name__] = schema
    return {"$ref": f"#/components/schemas/{model.__name__}"}


def build_openapi_document() -> dict:
    components: dict[str, dict] = {}
    error_ref = _schema_for(ApiErrorEnvelope, components)
    paths: dict[str, dict] = {}
    for operation in sorted(_OPERATIONS.values(), key=lambda item: (item.path, item.method)):
        response_ref = _schema_for(operation.response_model, components)
        entry = {
            "operationId": operation.operation_id,
            "summary": operation.summary,
            "security": [{"bearerAuth": []}],
            "x-required-scopes": list(operation.scopes),
            "responses": {
                str(operation.success_status): {
                    "description": "Success",
                    "content": {"application/json": {"schema": response_ref}},
                },
                "default": {
                    "description": "Error",
                    "content": {"application/json": {"schema": error_ref}},
                },
            },
        }
        if operation.request_model is not None:
            request_ref = _schema_for(operation.request_model, components)
            entry["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": request_ref}},
            }
        paths.setdefault(operation.path, {})[operation.method.lower()] = entry
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "BLD Matcher API",
            "version": "1.0.0",
            "description": "Stable API contract for internal tools and AI applications.",
        },
        "servers": [{"url": "/"}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "BLD API Key",
                }
            },
            "schemas": components,
        },
    }
