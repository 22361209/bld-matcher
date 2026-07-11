from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from flask import g, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ApiError(Exception):
    code: str
    message: str
    status: int = 400
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


def request_id() -> str:
    return str(getattr(g, "request_id", ""))


def success_response(data: Any, *, status: int = 200, warnings: list[str] | None = None):
    return (
        jsonify(
            {
                "api_version": "1",
                "request_id": request_id(),
                "data": data,
                "warnings": warnings or [],
            }
        ),
        status,
    )


def error_response(error: ApiError):
    return (
        jsonify(
            {
                "api_version": "1",
                "request_id": request_id(),
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                    "retryable": error.retryable,
                },
            }
        ),
        error.status,
    )


def validation_error(exc: ValidationError) -> ApiError:
    details = {
        "fields": [
            {
                "path": ".".join(str(part) for part in item["loc"]),
                "code": item["type"],
                "message": item["msg"],
            }
            for item in exc.errors(include_url=False, include_input=False)
        ]
    }
    return ApiError("request.invalid", "请求数据不符合接口约定。", 422, details)


def register_api_error_handlers(blueprint) -> None:
    @blueprint.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return error_response(error)

    @blueprint.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        return error_response(validation_error(error))

    @blueprint.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return error_response(
            ApiError(
                f"http.{error.code or 500}",
                str(error.description or "请求无法完成。"),
                int(error.code or 500),
            )
        )

    @blueprint.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        logger.exception(
            "Unhandled API exception",
            extra={"request_id": request_id(), "endpoint": request.endpoint, "method": request.method},
        )
        return error_response(
            ApiError(
                "internal.unexpected",
                "服务暂时无法完成请求。",
                500,
                retryable=True,
            )
        )
