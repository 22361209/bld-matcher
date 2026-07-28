from __future__ import annotations

from flask import Blueprint, request

from app.platform.api_auth import api_scope_required
from app.platform.api_errors import register_api_error_handlers, success_response
from app.platform.openapi import OpenApiOperation, register_openapi_operation

from .factory import get_customer_service
from .schemas import CustomerListData, CustomerListEnvelope, CustomerListQuery, CustomerResponse


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


def register(app) -> None:
    app.register_blueprint(customer_v1_api)
