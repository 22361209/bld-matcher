from __future__ import annotations

from functools import wraps

from flask import Blueprint, make_response, request

from app.platform.api_auth import api_actor_name, api_scope_required
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


quote_v1_api = Blueprint("quote_v1_api", __name__)
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
    app.register_blueprint(quote_v1_api)
