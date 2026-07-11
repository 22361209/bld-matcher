from __future__ import annotations

from functools import wraps
from typing import Any

from flask import request
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError

from .api_errors import ApiError, validation_error


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlatformInfo(StrictApiModel):
    name: str
    api_version: str
    capabilities: list[str]


class PlatformInfoEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: PlatformInfo
    warnings: list[str] = Field(default_factory=list)


class OpenApiDocument(RootModel[dict[str, Any]]):
    pass


class ApiSuccessEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class ApiErrorDetail(StrictApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class ApiErrorEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    error: ApiErrorDetail


def parse_json(model_type: type[StrictApiModel]) -> StrictApiModel:
    if not request.is_json:
        raise ApiError("request.content_type", "请求必须使用 application/json。", 415)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("request.invalid_json", "请求正文必须是 JSON 对象。", 400)
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise validation_error(exc) from exc


def api_schema(model_type: type[StrictApiModel]):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            kwargs["payload"] = parse_json(model_type)
            return fn(*args, **kwargs)

        setattr(wrapper, "__api_request_model__", model_type)
        return wrapper

    return decorator
