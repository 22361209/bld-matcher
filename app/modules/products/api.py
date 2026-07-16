from __future__ import annotations

from functools import wraps

from flask import Blueprint, request
from pydantic import ValidationError

from app.platform.api_auth import api_actor_name, api_scope_required
from app.platform.api_errors import ApiError, register_api_error_handlers, success_response, validation_error
from app.platform.api_schemas import api_schema
from app.platform.idempotency import idempotency_required
from app.platform.openapi import OpenApiOperation, register_openapi_operation

from .factory import get_product_service
from .schemas import (
    ProductPriceUpdateData,
    ProductPriceUpdateEnvelope,
    ProductPriceUpdateRequest,
    ProductResponse,
    ProductSearchData,
    ProductSearchEnvelope,
    ProductSearchQuery,
)
from .service import ProductNotFoundError, ProductVersionConflictError


product_v1_api = Blueprint("product_v1_api", __name__)
register_api_error_handlers(product_v1_api)


def _product_api_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ProductNotFoundError as exc:
            raise ApiError(
                "product.not_found",
                "产品不存在。",
                404,
                {"product_id": exc.product_id},
            ) from exc
        except ProductVersionConflictError as exc:
            raise ApiError(
                "product.version_conflict",
                str(exc),
                412,
                {"product_id": exc.product_id, "current_updated_at": exc.current_updated_at},
            ) from exc

    return wrapper


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


@product_v1_api.post("/api/v1/products/<int:product_id>/price")
@api_scope_required("products:write")
@idempotency_required
@api_schema(ProductPriceUpdateRequest)
@_product_api_errors
def update_product_price_v1(product_id: int, *, payload: ProductPriceUpdateRequest):
    product = get_product_service().update_price(
        product_id,
        price_cny=payload.price_cny,
        expected_updated_at=payload.expected_updated_at,
        actor=api_actor_name(),
    )
    data = ProductPriceUpdateData(product=ProductResponse.model_validate(product.api_payload()))
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

register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/products/{product_id}/price",
        method="POST",
        operation_id="updateProductPrice",
        summary="Update a product tax-inclusive unit price",
        scopes=("products:write",),
        request_model=ProductPriceUpdateRequest,
        response_model=ProductPriceUpdateEnvelope,
        path_parameters=(("product_id", "integer"),),
    )
)


def register(app) -> None:
    app.register_blueprint(product_v1_api)
