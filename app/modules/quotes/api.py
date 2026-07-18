from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, jsonify, make_response, request

from app.platform.api_auth import api_actor_name, api_scope_required, internal_api_required
from app.platform.api_errors import ApiError, register_api_error_handlers, success_response
from app.platform.api_schemas import api_schema
from app.platform.idempotency import idempotency_required
from app.platform.openapi import OpenApiOperation, register_openapi_operation
from app.platform.versioning import expected_version, if_match_required

from .domain import QuoteRecord, QuoteValidationError
from .factory import get_quote_service
from .schemas import (
    QuoteCreateRequest,
    QuoteEnvelope,
    QuoteLatestEnvelope,
    QuoteLatestQuery,
    QuoteListData,
    QuoteListEnvelope,
    QuoteListQuery,
    QuotePatchRequest,
    QuoteResponse,
)
from .service import QuoteNotFoundError, QuoteVersionConflictError


logger = logging.getLogger(__name__)
quote_v1_api = Blueprint("quote_v1_api", __name__)
quote_legacy_api = Blueprint("quote_legacy_api", __name__)
register_api_error_handlers(quote_v1_api)


def _quote_payload(record: QuoteRecord) -> dict:
    return QuoteResponse.model_validate(record.api_payload()).model_dump(mode="json")


def _quote_response(record: QuoteRecord, *, status: int = 200):
    response = make_response(success_response({"quote": _quote_payload(record)}, status=status))
    response.headers["ETag"] = f'"{record.version}"'
    return response


def _quote_api_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except QuoteValidationError as exc:
            raise ApiError(
                exc.code,
                exc.message,
                422,
                {"field": exc.field} if exc.field else {},
            ) from exc
        except QuoteNotFoundError as exc:
            raise ApiError(
                "quote.not_found",
                "报价记录不存在。",
                404,
                {"quote_id": exc.quote_id},
            ) from exc
        except QuoteVersionConflictError as exc:
            raise ApiError(
                "quote.version_conflict",
                str(exc),
                412,
                {
                    "quote_id": exc.quote_id,
                    "expected_version": exc.expected_version,
                    "current_version": exc.current_version,
                },
            ) from exc

    return wrapper


@quote_v1_api.get("/api/v1/quotes")
@api_scope_required("quotes:read")
@_quote_api_errors
def list_quotes_v1():
    query = QuoteListQuery.model_validate(request.args.to_dict())
    values = query.model_dump(exclude_none=True, mode="json")
    limit = int(values.pop("limit"))
    offset = int(values.pop("offset"))
    page = get_quote_service().list_records(values, limit=limit, offset=offset)
    data = QuoteListData(
        quotes=[QuoteResponse.model_validate(record.api_payload()) for record in page.records],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
    return success_response(data.model_dump(mode="json"))


@quote_v1_api.get("/api/v1/quotes/latest")
@api_scope_required("quotes:read")
@_quote_api_errors
def latest_quote_v1():
    query = QuoteLatestQuery.model_validate(request.args.to_dict())
    record = get_quote_service().latest(customer_name=query.customer_name, bld_no=query.bld_no)
    return _quote_response(record) if record else success_response({"quote": None})


@quote_v1_api.get("/api/v1/quotes/<int:quote_id>")
@api_scope_required("quotes:read")
@_quote_api_errors
def get_quote_v1(quote_id: int):
    return _quote_response(get_quote_service().get_record(quote_id))


@quote_v1_api.post("/api/v1/quotes")
@api_scope_required("quotes:write")
@idempotency_required
@api_schema(QuoteCreateRequest)
@_quote_api_errors
def create_quote_v1(*, payload: QuoteCreateRequest):
    actor = api_actor_name()
    values = payload.model_dump(exclude={"on_behalf_of"}, exclude_unset=True, mode="python")
    values.update({"quoted_by": actor, "source_type": "api"})
    record = get_quote_service().create(values, actor=actor)
    return _quote_response(record, status=201)


@quote_v1_api.patch("/api/v1/quotes/<int:quote_id>")
@api_scope_required("quotes:write")
@idempotency_required
@if_match_required
@api_schema(QuotePatchRequest)
@_quote_api_errors
def update_quote_v1(quote_id: int, *, payload: QuotePatchRequest):
    values = payload.model_dump(exclude={"on_behalf_of"}, exclude_unset=True, mode="python")
    record = get_quote_service().update(
        quote_id,
        values,
        actor=api_actor_name(),
        expected_version=expected_version(),
    )
    return _quote_response(record)


def _legacy_payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return dict(request.form)


def _legacy_filters() -> dict[str, str]:
    return {
        "customer_name": request.args.get("customer_name", request.args.get("customer", "")).strip(),
        "bld_no": request.args.get(
            "bld_no",
            request.args.get("product_model", request.args.get("model", "")),
        ).strip(),
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
        "currency": request.args.get("currency", "").strip().upper(),
        "quoted_by": request.args.get("quoted_by", "").strip(),
    }


def _legacy_limit(default: int = 100) -> int:
    try:
        return max(1, min(500, int(request.args.get("limit", default))))
    except ValueError:
        return default


def _legacy_offset() -> int:
    try:
        return max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return 0


def _legacy_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


@quote_legacy_api.post("/api/quotes")
@internal_api_required
def api_create_quote():
    actor = api_actor_name()
    values = _legacy_payload()
    values.update({"quoted_by": actor, "source_type": "api"})
    try:
        record = get_quote_service().create(values, actor=actor)
    except QuoteValidationError as exc:
        return _legacy_error(exc.message)
    except Exception:
        logger.exception("Legacy quote create failed")
        return _legacy_error("报价服务暂时无法保存记录。", 500)
    return jsonify({"ok": True, "quote": record.legacy_payload()}), 201


@quote_legacy_api.get("/api/quotes")
@internal_api_required
def api_list_quotes():
    try:
        page = get_quote_service().list_records(
            _legacy_filters(),
            limit=_legacy_limit(),
            offset=_legacy_offset(),
        )
    except QuoteValidationError as exc:
        return _legacy_error(exc.message)
    except Exception:
        logger.exception("Legacy quote list failed")
        return _legacy_error("报价服务暂时无法查询记录。", 500)
    return jsonify({"ok": True, "quotes": [record.legacy_payload() for record in page.records]})


@quote_legacy_api.get("/api/quotes/latest")
@internal_api_required
def api_latest_quote():
    try:
        record = get_quote_service().latest(
            customer_name=request.args.get("customer_name", ""),
            bld_no=request.args.get("bld_no", request.args.get("product_model", "")),
        )
    except QuoteValidationError as exc:
        return _legacy_error(exc.message)
    except Exception:
        logger.exception("Legacy latest quote lookup failed")
        return _legacy_error("报价服务暂时无法查询记录。", 500)
    return jsonify({"ok": True, "quote": record.legacy_payload() if record else None})


@quote_legacy_api.put("/api/quotes/<int:quote_id>")
@internal_api_required
def api_update_quote(quote_id: int):
    try:
        record = get_quote_service().update(
            quote_id,
            _legacy_payload(),
            actor=api_actor_name(),
        )
    except QuoteValidationError as exc:
        return _legacy_error(exc.message)
    except QuoteNotFoundError:
        return _legacy_error("报价记录不存在。", 404)
    except Exception:
        logger.exception("Legacy quote update failed")
        return _legacy_error("报价服务暂时无法修正记录。", 500)
    return jsonify({"ok": True, "quote": record.legacy_payload()})


register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/quotes",
        method="GET",
        operation_id="listQuotes",
        summary="List quote records",
        scopes=("quotes:read",),
        response_model=QuoteListEnvelope,
        query_model=QuoteListQuery,
    )
)
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/quotes/latest",
        method="GET",
        operation_id="getLatestQuote",
        summary="Read the latest quote for a customer and BLD number",
        scopes=("quotes:read",),
        response_model=QuoteLatestEnvelope,
        query_model=QuoteLatestQuery,
    )
)
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/quotes/{quote_id}",
        method="GET",
        operation_id="getQuote",
        summary="Read a quote record",
        scopes=("quotes:read",),
        response_model=QuoteEnvelope,
        path_parameters=(("quote_id", "integer"),),
        response_headers=(("ETag", "string"),),
    )
)
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/quotes",
        method="POST",
        operation_id="createQuote",
        summary="Create a quote record",
        scopes=("quotes:write",),
        response_model=QuoteEnvelope,
        request_model=QuoteCreateRequest,
        response_headers=(("ETag", "string"),),
        success_status=201,
    )
)
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/quotes/{quote_id}",
        method="PATCH",
        operation_id="updateQuote",
        summary="Update a quote record with optimistic concurrency",
        scopes=("quotes:write",),
        response_model=QuoteEnvelope,
        request_model=QuotePatchRequest,
        path_parameters=(("quote_id", "integer"),),
        response_headers=(("ETag", "string"),),
    )
)


def register(app) -> None:
    app.register_blueprint(quote_legacy_api)
    app.register_blueprint(quote_v1_api)
