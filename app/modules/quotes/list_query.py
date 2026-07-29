from __future__ import annotations

from math import ceil
from urllib.parse import urlencode

from flask import request, url_for


QUOTE_COLUMN_FILTER_FIELDS = (
    "quote_no",
    "quote_date",
    "customer_name",
    "bld_no",
    "customer_product_code",
    "tax_price",
    "net_price",
    "currency",
    "quoted_by",
    "source_type",
    "remark",
)


def filters_from_request() -> dict[str, object]:
    column_filters = {
        field: tuple(value for value in request.args.getlist(f"qf_{field}") if value)
        for field in QUOTE_COLUMN_FILTER_FIELDS
    }
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
        "quote_no": request.args.get("quote_no", "").strip(),
        "column_filters": {field: values for field, values in column_filters.items() if values},
    }


def requested_page() -> int:
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def page_url(filters: dict[str, object], page: int) -> str:
    params = [(key, str(value)) for key, value in filters.items() if key != "column_filters" and value]
    for field, values in dict(filters.get("column_filters", {})).items():
        params.extend((f"qf_{field}", str(value)) for value in values)
    if page > 1:
        params.append(("page", str(page)))
    query = urlencode(params)
    return f"{url_for('quote_web.quotes')}{'?' + query if query else ''}#quote-results"


def pagination(filters: dict[str, object], page: int, total: int, *, page_size: int) -> dict[str, object]:
    total_pages = max(1, ceil(total / page_size))
    page = min(max(1, page), total_pages)
    start = ((page - 1) * page_size) + 1 if total else 0
    end = min(total, page * page_size)
    window = {1, total_pages, page - 1, page, page + 1}
    links = []
    previous_page = 0
    for item in sorted(value for value in window if 1 <= value <= total_pages):
        if previous_page and item - previous_page > 1:
            links.append({"gap": True})
        links.append({"page": item, "url": page_url(filters, item), "current": item == page})
        previous_page = item
    return {
        "page": page,
        "total_pages": total_pages,
        "start": start,
        "end": end,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": page_url(filters, page - 1) if page > 1 else "",
        "next_url": page_url(filters, page + 1) if page < total_pages else "",
        "jump_url": page_url(filters, 1),
        "links": links,
    }
