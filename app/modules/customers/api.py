from __future__ import annotations

from flask import Blueprint, request

from app.platform.api_auth import api_actor_name, api_scope_required
from app.platform.api_errors import ApiError, register_api_error_handlers, success_response
from app.platform.api_schemas import api_schema
from app.platform.idempotency import idempotency_required
from app.platform.openapi import OpenApiOperation, register_openapi_operation

from .domain import CustomerValidationError
from .factory import get_customer_service
from .schemas import (
    CustomerCreateRequest,
    CustomerData,
    CustomerDetailResponse,
    CustomerEnvelope,
    CustomerListData,
    CustomerListEnvelope,
    CustomerListQuery,
    CustomerResponse,
)


customer_v1_api = Blueprint("customer_v1_api", __name__)
register_api_error_handlers(customer_v1_api)


@customer_v1_api.get("/api/v1/customers")
@api_scope_required("quotes:read")
def list_customers_v1():
    query = CustomerListQuery.model_validate(request.args.to_dict())
    matches = get_customer_service().lookup(query.q, limit=query.limit)
    data = CustomerListData(
        customers=[CustomerResponse(name=customer.name) for customer in matches],
        total=len(matches),
    )
    return success_response(data.model_dump(mode="json"))


@customer_v1_api.post("/api/v1/customers")
@api_scope_required("quotes:write")
@idempotency_required
@api_schema(CustomerCreateRequest)
def create_customer_v1(*, payload: CustomerCreateRequest):
    try:
        customer = get_customer_service().create(payload.name, actor=api_actor_name())
    except CustomerValidationError as exc:
        status = 409 if exc.code == "customer.duplicate" else 422
        raise ApiError(exc.code, exc.message, status) from exc
    data = CustomerData(
        customer=CustomerDetailResponse(
            id=customer.id,
            name=customer.name,
            code=customer.code,
            status=customer.status,
            created_at=customer.created_at,
            updated_at=customer.updated_at,
        )
    )
    return success_response(data.model_dump(mode="json"), status=201)


register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/customers",
        method="GET",
        operation_id="listCustomers",
        summary="Match registered customers by name fragment",
        scopes=("quotes:read",),
        response_model=CustomerListEnvelope,
        query_model=CustomerListQuery,
    )
)
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/customers",
        method="POST",
        operation_id="createCustomer",
        summary="Create a registered customer",
        scopes=("quotes:write",),
        response_model=CustomerEnvelope,
        request_model=CustomerCreateRequest,
        success_status=201,
    )
)


def register(app) -> None:
    app.register_blueprint(customer_v1_api)
