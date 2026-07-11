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
    response_model: type[BaseModel] | None = ApiSuccessEnvelope
    response_media_type: str = "application/json"
    response_schema: dict | None = None
    request_model: type[BaseModel] | None = None
    query_model: type[BaseModel] | None = None
    path_parameters: tuple[tuple[str, str], ...] = ()
    header_parameters: tuple[tuple[str, str, bool], ...] = ()
    response_headers: tuple[tuple[str, str], ...] = ()
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


def _query_parameters(model: type[BaseModel], components: dict[str, dict]) -> list[dict]:
    schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    components.update(schema.pop("$defs", {}))
    required = set(schema.get("required", []))
    return [
        {
            "name": name,
            "in": "query",
            "required": name in required,
            "schema": property_schema,
        }
        for name, property_schema in schema.get("properties", {}).items()
    ]


def build_openapi_document() -> dict:
    components: dict[str, dict] = {}
    error_ref = _schema_for(ApiErrorEnvelope, components)
    paths: dict[str, dict] = {}
    for operation in sorted(_OPERATIONS.values(), key=lambda item: (item.path, item.method)):
        response_ref = (
            _schema_for(operation.response_model, components)
            if operation.response_model is not None
            else dict(operation.response_schema or {"type": "string", "format": "binary"})
        )
        entry = {
            "operationId": operation.operation_id,
            "summary": operation.summary,
            "security": [{"bearerAuth": []}],
            "x-required-scopes": list(operation.scopes),
            "responses": {
                str(operation.success_status): {
                    "description": "Success",
                    "content": {operation.response_media_type: {"schema": response_ref}},
                },
                "default": {
                    "description": "Error",
                    "content": {"application/json": {"schema": error_ref}},
                },
            },
        }
        if operation.response_headers:
            entry["responses"][str(operation.success_status)]["headers"] = {
                name: {"schema": {"type": header_type}}
                for name, header_type in operation.response_headers
            }
        if operation.request_model is not None:
            request_ref = _schema_for(operation.request_model, components)
            entry["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": request_ref}},
            }
        parameters = [
            {
                "name": name,
                "in": "path",
                "required": True,
                "schema": {"type": parameter_type},
            }
            for name, parameter_type in operation.path_parameters
        ]
        if operation.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            parameters.append(
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "minLength": 8, "maxLength": 128},
                }
            )
        if operation.method.upper() == "PATCH":
            parameters.append(
                {
                    "name": "If-Match",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string"},
                }
            )
        parameters.extend(
            {
                "name": name,
                "in": "header",
                "required": required,
                "schema": {"type": parameter_type},
            }
            for name, parameter_type, required in operation.header_parameters
        )
        if operation.query_model is not None:
            parameters.extend(_query_parameters(operation.query_model, components))
        if parameters:
            entry["parameters"] = parameters
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
