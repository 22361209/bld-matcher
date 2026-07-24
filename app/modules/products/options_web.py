from __future__ import annotations

from flask import jsonify, request

from app.modules.products.domain import (
    ProductFilters,
    ProductFilterValidationError,
    build_product_filters,
)
from app.modules.products.factory import get_product_service
from app.product_status import format_product_status
from app.security import login_required


def register(app) -> None:
    @app.get("/products/options")
    @login_required
    def product_options():
        options = get_product_service().filter_options(ProductFilters(status="all")).web_payload()
        response = jsonify(
            {
                "brands": [option["label"] for option in options["brand"] if option["value"]],
                "items": [option["label"] for option in options["item"] if option["value"]],
                "statuses": [
                    format_product_status(option["value"], "zh", multiline=False)
                    for option in options["product_status"]
                    if option["value"]
                ],
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/products/lookup")
    @login_required
    def product_lookup():
        try:
            filters = build_product_filters({"q": request.args.get("q", ""), "status": "all"})
        except ProductFilterValidationError as exc:
            return jsonify({"ok": False, "error": f"筛选条件无效：{exc}"}), 400
        page = get_product_service().search(filters, limit=20, offset=0)
        response = jsonify(
            [
                {
                    "id": record.id,
                    "bld_no": record.bld_no,
                    "item": record.item,
                    "series": record.series,
                }
                for record in page.records
            ]
        )
        response.headers["Cache-Control"] = "no-store"
        return response
