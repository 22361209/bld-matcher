from __future__ import annotations

import logging
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, request

from app.helpers import clean_original_filename
from app.platform.api_auth import (
    api_actor_name,
    api_scope_required,
    current_api_principal,
    internal_api_required,
)
from app.platform.api_errors import ApiError, register_api_error_handlers, success_response
from app.platform.api_schemas import api_schema
from app.platform.idempotency import idempotency_required
from app.platform.openapi import OpenApiOperation, register_openapi_operation

from .domain import CatalogUnavailableError, InquiryValidationError, parse_bool, payload_value
from .factory import get_inquiry_service
from .infrastructure import ALLOWED_WORKBOOK_SUFFIXES
from .schemas import InquiryAnalyzeRequest, InquiryData, InquiryEnvelope, InquiryExportRequest
from .service import InquiryWorkbookError


logger = logging.getLogger(__name__)
inquiry_v1_api = Blueprint("inquiry_v1_api", __name__)
inquiry_legacy_api = Blueprint("inquiry_legacy_api", __name__)
register_api_error_handlers(inquiry_v1_api)


def _inquiry_api_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except InquiryValidationError as exc:
            raise ApiError(exc.code, exc.message, 422, exc.details) from exc
        except CatalogUnavailableError as exc:
            raise ApiError("catalog.unavailable", str(exc), 409) from exc
        except InquiryWorkbookError as exc:
            raise ApiError(
                "inquiry.workbook_invalid",
                str(exc),
                422,
                {"column_preview": exc.column_preview} if exc.column_preview else {},
            ) from exc

    return wrapper


def _principal_subject() -> str:
    principal = current_api_principal()
    if principal is None:
        raise RuntimeError("Inquiry v1 route requires an API principal.")
    return principal.subject


@inquiry_v1_api.post("/api/v1/inquiries/analyze")
@api_scope_required("inquiries:run")
@idempotency_required
@api_schema(InquiryAnalyzeRequest)
@_inquiry_api_errors
def analyze_inquiry_v1(*, payload: InquiryAnalyzeRequest):
    execution = get_inquiry_service().run_numbers(
        payload.model_dump(mode="python"),
        export=False,
        actor=api_actor_name(),
    )
    data = InquiryData.model_validate(execution.api_payload())
    return success_response(data.model_dump(mode="json"))


@inquiry_v1_api.post("/api/v1/inquiries/export")
@api_scope_required("inquiries:run")
@idempotency_required
@api_schema(InquiryExportRequest)
@_inquiry_api_errors
def export_inquiry_v1(*, payload: InquiryExportRequest):
    execution = get_inquiry_service().run_numbers(
        payload.model_dump(mode="python"),
        export=True,
        actor=api_actor_name(),
        artifact_owner=_principal_subject(),
    )
    data = InquiryData.model_validate(execution.api_payload())
    return success_response(data.model_dump(mode="json"), status=201)


def _legacy_payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    form_payload: dict[str, object] = {}
    for key in request.form:
        values = request.form.getlist(key)
        form_payload[key] = values if len(values) > 1 else values[0]
    return form_payload


def _legacy_error(message: str, status: int = 400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


def _legacy_source(payload: dict) -> tuple[Path, str]:
    service = get_inquiry_service()
    file = request.files.get("file") or request.files.get("inquiry")
    if file and file.filename:
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_WORKBOOK_SUFFIXES:
            raise InquiryValidationError(
                "inquiry.invalid_file_type",
                "客户原始文件仅支持 .xls 或 .xlsx。",
            )
        source_path = service.engine.internal_upload_path(file.filename, prefix="source")
        file.save(source_path)
        return source_path, clean_original_filename(file.filename, fallback_suffix=suffix)
    raw_path = payload_value(payload, "file_path", "path", "source_path")
    try:
        source_path = service.engine.resolve_source_path(raw_path)
    except ValueError as exc:
        raise InquiryValidationError("inquiry.invalid_source", str(exc)) from exc
    return source_path, clean_original_filename(source_path.name, fallback_suffix=source_path.suffix)


def _run_legacy_numbers(payload: dict, *, export: bool):
    try:
        execution = get_inquiry_service().run_numbers(
            payload,
            export=export,
            actor=api_actor_name(),
        )
        return jsonify(execution.legacy_payload())
    except InquiryValidationError as exc:
        return _legacy_error(exc.message, invalid_items=exc.details.get("invalid_items", []))
    except CatalogUnavailableError as exc:
        return _legacy_error(str(exc), 409)
    except Exception:
        logger.exception("Legacy inquiry number request failed")
        return _legacy_error("询价服务暂时无法完成号码查询。", 500)


def _run_legacy_file(payload: dict, *, export: bool):
    try:
        source_path, original_filename = _legacy_source(payload)
        execution = get_inquiry_service().run_file(
            source_path,
            original_filename,
            payload,
            export=export,
            actor=api_actor_name(),
        )
        return jsonify(execution.legacy_payload())
    except InquiryValidationError as exc:
        return _legacy_error(exc.message)
    except CatalogUnavailableError as exc:
        return _legacy_error(str(exc), 409)
    except InquiryWorkbookError as exc:
        return _legacy_error(str(exc), 422, column_preview=exc.column_preview)
    except Exception:
        logger.exception("Legacy inquiry file request failed")
        return _legacy_error("询价服务暂时无法分析客户文件。", 500)


@inquiry_legacy_api.post("/api/internal/inquiry/numbers")
@internal_api_required
def internal_inquiry_numbers():
    payload = _legacy_payload()
    export = parse_bool(payload_value(payload, "export"), default=False)
    return _run_legacy_numbers(payload, export=export)


@inquiry_legacy_api.post("/api/internal/inquiry/file")
@internal_api_required
def internal_inquiry_file():
    payload = _legacy_payload()
    export = parse_bool(payload_value(payload, "export"), default=False)
    return _run_legacy_file(payload, export=export)


@inquiry_legacy_api.post("/api/internal/inquiry/analyze")
@internal_api_required
def internal_inquiry_analyze():
    payload = _legacy_payload()
    if request.files or payload_value(payload, "file_path", "path", "source_path"):
        return _run_legacy_file(payload, export=False)
    return _run_legacy_numbers(payload, export=False)


register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/inquiries/analyze",
        method="POST",
        operation_id="analyzeInquiry",
        summary="Analyze product numbers without creating an output file",
        scopes=("inquiries:run",),
        request_model=InquiryAnalyzeRequest,
        response_model=InquiryEnvelope,
    )
)
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/inquiries/export",
        method="POST",
        operation_id="exportInquiry",
        summary="Analyze product numbers and create a principal-owned artifact",
        scopes=("inquiries:run",),
        request_model=InquiryExportRequest,
        response_model=InquiryEnvelope,
        success_status=201,
    )
)


def register(app) -> None:
    app.register_blueprint(inquiry_legacy_api)
    app.register_blueprint(inquiry_v1_api)
