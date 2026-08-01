from __future__ import annotations

import logging

from flask import flash, jsonify, redirect, render_template, request, send_file, url_for

from app.helpers import (
    all_recent_outputs,
    product_image_thumb_url,
    product_image_url,
    user_file_label,
    user_output_dir,
    user_recent_outputs,
)
from app.security import actor_name, can, permission_required

from .factory import get_contract_service


logger = logging.getLogger(__name__)


def _render_contract_management(mode: str):
    output_reader = all_recent_outputs if can("manage_users") else user_recent_outputs
    context = get_contract_service().page_context(
        mode=mode,
        user_label=user_file_label(),
        output_reader=output_reader,
        history_type=request.args.get("contract_type", "all"),
        history_query=request.args.get("contract_q", ""),
        source_quote_no=request.args.get("source_quote_no", ""),
        quote_ids=request.args.getlist("quote_id"),
        language=request.args.get("language", ""),
    )
    return render_template("purchase_contracts.html", **context)


def register(app) -> None:
    @app.get("/contracts")
    @permission_required("generate_purchase_contract")
    def contracts():
        return _render_contract_management("purchase")

    @app.get("/purchase-contracts")
    @permission_required("generate_purchase_contract")
    def purchase_contracts():
        return _render_contract_management("purchase")

    @app.get("/contracts/sales")
    @permission_required("generate_purchase_contract")
    def sales_contracts():
        try:
            return _render_contract_management("sales")
        except ValueError as exc:
            flash(f"无法生成销售合同草稿：{exc}", "error")
            return redirect(url_for("quote_web.quotes") + "#quote-results")

    @app.get("/contracts/documents/<int:document_id>/download")
    @permission_required("generate_purchase_contract")
    def download_contract_document(document_id: int):
        resolved = get_contract_service().document_path(document_id)
        if resolved is None:
            flash("销售合同文件不存在或无权访问。", "error")
            return redirect(url_for("quote_web.quotes") + "#quote-results")
        path, document = resolved
        return send_file(path, as_attachment=True, download_name=str(document["name"]))

    @app.get("/purchase-contracts/product-lookup")
    @permission_required("generate_purchase_contract")
    def purchase_contract_product_lookup():
        product = get_contract_service().lookup_product(request.args.get("bld", ""))
        if not product:
            return jsonify({"found": False})
        image_url = product_image_url(product)
        thumb_url = product_image_thumb_url(product)
        payload = {
            "found": True,
            "bld_no": product["bld_no"],
            "oe_no": product.get("oe_no_1") or "",
            "product_name": product.get("item") or "",
            "models": product.get("models") or "",
            "image_url": image_url,
            "thumb_url": thumb_url or image_url,
        }
        if can("manage_customer_prices"):
            payload["price_cny"] = product.get("price_cny")
        return jsonify(payload)

    @app.post("/purchase-contracts/generate")
    @permission_required("generate_purchase_contract")
    def generate_purchase_contract():
        try:
            output_path = get_contract_service().generate(
                "purchase",
                request.form,
                output_root=user_output_dir(),
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("contracts"))
        except Exception:
            logger.exception("Purchase contract generation failed")
            flash("生成失败，请稍后重试。", "error")
            return redirect(url_for("contracts"))
        return send_file(output_path, as_attachment=True, download_name=output_path.name)

    @app.post("/sales-contracts/generate")
    @permission_required("generate_purchase_contract")
    def generate_sales_contract():
        try:
            output_path = get_contract_service().generate(
                "sales",
                request.form,
                output_root=user_output_dir(),
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("sales_contracts"))
        except Exception:
            logger.exception("Sales contract generation failed")
            flash("生成失败，请稍后重试。", "error")
            return redirect(url_for("sales_contracts"))
        return send_file(output_path, as_attachment=True, download_name=output_path.name)
