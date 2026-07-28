from __future__ import annotations

from math import ceil
from urllib.parse import urlencode

from flask import request, url_for

from .domain import MATERIAL_COLUMN_FILTER_FIELDS


MATERIAL_COLUMN_FILTER_ORDER = (
    "model",
    "code",
    "category",
    "car",
    "part",
    "spec_text",
    "pieces",
    "unit_weight",
    "active",
)


def column_filters_from_request() -> dict[str, tuple[str, ...]]:
    return {
        field: tuple(value for value in request.args.getlist(f"mf_{field}") if value)
        for field in MATERIAL_COLUMN_FILTER_ORDER
        if field in MATERIAL_COLUMN_FILTER_FIELDS and request.args.getlist(f"mf_{field}")
    }


def page_url(
    query: str,
    status: str,
    page: int,
    column_filters: dict[str, tuple[str, ...]] | None = None,
) -> str:
    params: list[tuple[str, str]] = []
    if query.strip():
        params.append(("q", query))
    if status != "active":
        params.append(("status", status))
    for field, values in (column_filters or {}).items():
        params.extend((f"mf_{field}", value) for value in values)
    if page > 1:
        params.append(("page", str(page)))
    query_string = urlencode(params)
    return f"{url_for('material_items')}{'?' + query_string if query_string else ''}#materials-results"


def pagination(
    query: str,
    status: str,
    page: int,
    total: int,
    *,
    page_size: int,
    column_filters: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, object]:
    total_pages = max(1, ceil(total / page_size))
    page = min(max(1, page), total_pages)
    start = ((page - 1) * page_size) + 1 if total else 0
    end = min(total, page * page_size)
    window = {1, total_pages, page - 1, page, page + 1}
    pages = sorted(item for item in window if 1 <= item <= total_pages)
    links = []
    previous_page = 0
    for item in pages:
        if previous_page and item - previous_page > 1:
            links.append({"gap": True})
        links.append({"page": item, "url": page_url(query, status, item, column_filters), "current": item == page})
        previous_page = item
    return {
        "page": page,
        "total_pages": total_pages,
        "start": start,
        "end": end,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": page_url(query, status, page - 1, column_filters) if page > 1 else "",
        "next_url": page_url(query, status, page + 1, column_filters) if page < total_pages else "",
        "links": links,
    }
