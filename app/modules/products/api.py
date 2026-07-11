from __future__ import annotations

from flask import Blueprint, request
from pydantic import ValidationError

from app.platform.api_auth import api_scope_required
from app.platform.api_errors import register_api_error_handlers, success_response, validation_error
from app.platform.openapi import OpenApiOperation, register_openapi_operation

from .factory import get_product_service
from .schemas import ProductResponse, ProductSearchData, ProductSearchEnvelope, ProductSearchQuery


product_v1_api = Blueprint("product_v1_api", __name__)
register_api_error_handlers(product_v1_api)


@product_v1_api.get("/api/v1/products/search")
@api_scope_required("products:read")
def search_products_v1():
    try:
        query = ProductSearchQuery.model_validate(request.args.to_dict())
    except ValidationError as exc:
        raise validation_error(exc) from exc
    values = query.model_dump(mode="python")
    limit = int(values.pop("limit"))
    offset = int(values.pop("offset"))
    page = get_product_service().search(values, limit=limit, offset=offset)
    data = ProductSearchData(
        products=[ProductResponse.model_validate(record.api_payload()) for record in page.records],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
    return success_response(data.model_dump(mode="json"))


register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/products/search",
        method="GET",
        operation_id="searchProducts",
        summary="Search the product catalog",
        scopes=("products:read",),
        response_model=ProductSearchEnvelope,
        query_model=ProductSearchQuery,
    )
)


def register(app) -> None:
    app.register_blueprint(product_v1_api)
