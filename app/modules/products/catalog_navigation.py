from __future__ import annotations

from math import ceil
from typing import Any

from flask import request, url_for

from .domain import ProductFilters, build_product_filters

PRODUCT_PAGE_SIZE = 50

def _product_filters(values: Any, *, default_status: str) -> ProductFilters:
    return build_product_filters(
        {
            "q": values.get("q", ""),
            "bld": values.get("bld", ""),
            "oe": values.get("oe", ""),
            "status": values.get("status", default_status),
            "language": values.get("lang", "en"),
            "visibility": values.get("visibility", "visible" if default_status == "active" else "all"),
            "brand": values.getlist("brand"),
            "item": values.getlist("item"),
            "product_status": values.getlist("product_status"),
        }
    )


def _product_query_args() -> ProductFilters:
    return _product_filters(request.args, default_status="active")


def _request_page() -> int:
    try:
        return max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        return 1


def _product_page_params(filters: ProductFilters, page: int) -> dict[str, Any]:
    params: dict[str, Any] = {"lang": filters.language, "visibility": filters.visibility}
    if filters.oe_query:
        params["oe"] = filters.oe_query
    elif filters.bld_query:
        params["bld"] = filters.bld_query
    elif filters.query:
        params["q"] = filters.query
    if filters.status != "active":
        params["status"] = filters.status
    brand_values = [*filters.brands, *(("",) if filters.brand_blank else ())]
    item_values = [*filters.items, *(("",) if filters.item_blank else ())]
    product_status_values = [
        *filters.product_statuses,
        *(("",) if filters.product_status_blank else ()),
    ]
    if brand_values:
        params["brand"] = brand_values
    if item_values:
        params["item"] = item_values
    if product_status_values:
        params["product_status"] = product_status_values
    if page > 1:
        params["page"] = page
    return params


def _product_page_url(filters: ProductFilters, page: int) -> str:
    params = _product_page_params(filters, page)
    return f"{url_for('products', **params)}#products-results"


def _product_brand_preview_url(filters: ProductFilters, page: int) -> str:
    params = _product_page_params(filters, page)
    params["brand_preview"] = "1"
    return f"{url_for('products', **params)}#products-results"


def _product_pagination(filters: ProductFilters, page: int, total: int) -> dict[str, Any]:
    total_pages = max(1, ceil(total / PRODUCT_PAGE_SIZE))
    page = min(max(1, page), total_pages)
    start = ((page - 1) * PRODUCT_PAGE_SIZE) + 1 if total else 0
    end = min(total, page * PRODUCT_PAGE_SIZE)
    window = {1, total_pages, page - 1, page, page + 1}
    pages = sorted(item for item in window if 1 <= item <= total_pages)
    links = []
    previous_page = 0
    for item in pages:
        if previous_page and item - previous_page > 1:
            links.append({"gap": True})
        links.append({"page": item, "url": _product_page_url(filters, item), "current": item == page})
        previous_page = item
    return {
        "page": page,
        "total_pages": total_pages,
        "start": start,
        "end": end,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": _product_page_url(filters, page - 1) if page > 1 else "",
        "next_url": _product_page_url(filters, page + 1) if page < total_pages else "",
        "jump_url": _product_page_url(filters, 1),
        "links": links,
    }
