from __future__ import annotations

import logging

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.modules.products.domain import (
    ProductFilterValidationError,
    build_product_filters,
)
from app.modules.products.factory import get_product_service
from app.product_status import format_product_status
from app.security import actor_name, login_required, permission_required


logger = logging.getLogger(__name__)


def register(app) -> None:
    @app.get("/products/options")
    @login_required
    def product_options():
        values = get_product_service().option_values()
        response = jsonify(
            {
                "brands": [option.value for option in values if option.kind == "brand"],
                "items": [option.value for option in values if option.kind == "item"],
                "statuses": [
                    format_product_status(option.value, "zh", multiline=False)
                    for option in values
                    if option.kind == "product_status"
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
        include_details = request.args.get("details") == "1"
        rows = []
        for record in page.records:
            row = {
                "id": record.id,
                "bld_no": record.bld_no,
                "item": record.item,
                "series": record.series,
            }
            if include_details:
                row.update(
                    {
                        "price_cny": record.price_cny,
                        "product_status": format_product_status(record.product_status, "zh"),
                    }
                )
            rows.append(row)
        response = jsonify(rows)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/product-options")
    @permission_required("manage_product_options")
    def product_option_values():
        values = get_product_service().option_values()
        return render_template(
            "product_options.html",
            brands=[option for option in values if option.kind == "brand"],
            items=[option for option in values if option.kind == "item"],
            statuses=[option for option in values if option.kind == "product_status"],
        )

    @app.post("/product-options/save")
    @permission_required("manage_product_options")
    def save_product_option_value_route():
        kind = request.form.get("kind", "").strip()
        value = request.form.get("value", "")
        option_id_text = request.form.get("id", "").strip()
        option_id = int(option_id_text) if option_id_text.isdigit() else None
        try:
            get_product_service().save_option_value(kind, value, option_id=option_id, actor=actor_name())
        except ValueError as exc:
            flash(f"候选值保存失败：{exc}", "error")
            return redirect(url_for("product_option_values"))
        except Exception:
            logger.exception("Product option value save failed")
            flash("候选值保存失败，请稍后重试。", "error")
            return redirect(url_for("product_option_values"))
        flash("候选值已保存。", "success")
        return redirect(url_for("product_option_values"))

    @app.post("/product-options/delete")
    @permission_required("manage_product_options")
    def delete_product_option_value_route():
        option_id_text = request.form.get("id", "").strip()
        option_id = int(option_id_text) if option_id_text.isdigit() else None
        try:
            if option_id is None:
                raise ValueError("缺少候选值编号。")
            get_product_service().delete_option_value(option_id, actor=actor_name())
        except ValueError as exc:
            flash(f"候选值删除失败：{exc}", "error")
            return redirect(url_for("product_option_values"))
        except Exception:
            logger.exception("Product option value delete failed")
            flash("候选值删除失败，请稍后重试。", "error")
            return redirect(url_for("product_option_values"))
        flash("候选值已删除，仅影响未来可选；已有产品数据保持不变。", "success")
        return redirect(url_for("product_option_values"))
