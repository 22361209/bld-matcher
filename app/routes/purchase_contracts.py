from __future__ import annotations

from datetime import date
from pathlib import Path

from flask import flash, jsonify, redirect, render_template, request, send_file, url_for

from app.config import BASE_DIR, CATALOG_PATH, DB_PATH, PRODUCT_IMAGE_DATA_PREFIX, PRODUCT_IMAGE_DIR
from app.database import bootstrap_from_excel, connect, log_event
from app.drawings import safe_filename_part
from app.helpers import (
    all_recent_outputs,
    product_image_thumb_url,
    product_image_url,
    unique_prefixed_path,
    user_file_label,
    user_output_dir,
    user_recent_outputs,
)
from app.product_media import resolve_product_image_path, resolve_product_image_thumb_path
from app.purchase_contract import (
    DEFAULT_BUYER_NAME,
    DEFAULT_DELIVERY_ADDRESS,
    DEFAULT_PAYMENT_TERMS,
    DEFAULT_PRICE_NOTE,
    DEFAULT_QUALITY_TERMS,
    default_contract_no,
    generate_purchase_contract_pdf,
    purchase_contract_from_form,
)
from app.security import actor_name, can, permission_required


def _product_by_bld(conn, bld_no: str):
    return conn.execute(
        "SELECT * FROM products WHERE UPPER(bld_no) = UPPER(?) AND active = 1",
        (bld_no.strip(),),
    ).fetchone()


def _existing(path: Path | None) -> Path | None:
    return path if path and path.exists() and path.is_file() else None


def _product_pdf_image_path(product) -> Path | None:
    explicit = (product["image_path"] if "image_path" in product.keys() else "") or ""
    if explicit.startswith(PRODUCT_IMAGE_DATA_PREFIX):
        name = explicit[len(PRODUCT_IMAGE_DATA_PREFIX) :]
        return _existing(resolve_product_image_thumb_path(name)) or _existing(resolve_product_image_path(name))
    if explicit.startswith("/static/"):
        return _existing(BASE_DIR / explicit.lstrip("/"))
    if explicit:
        return _existing(BASE_DIR / "static" / explicit.lstrip("/")) or _existing(PRODUCT_IMAGE_DIR / Path(explicit).name)

    bld_no = product["bld_no"]
    for suffix in ("jpg", "jpeg", "png", "webp"):
        candidates = [
            PRODUCT_IMAGE_DIR / f"{bld_no}.{suffix}",
            BASE_DIR / "static" / "product_images" / "thumbs" / f"{bld_no}.{suffix}",
            BASE_DIR / "static" / "product_images" / f"{bld_no}.{suffix}",
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def _apply_catalog_values(conn, contract: dict) -> None:
    for item in contract["items"]:
        product = _product_by_bld(conn, item["product_code"])
        if not product:
            continue
        item["product_code"] = product["bld_no"]
        item["oe_no"] = product["oe_no_1"] or item.get("oe_no", "")
        item["product_name"] = product["item"] or item.get("product_name", "")
        item["models"] = product["models"] or item.get("models", "")
        image_path = _product_pdf_image_path(product)
        item["image_path"] = str(image_path) if image_path else ""


def register(app) -> None:
    @app.get("/purchase-contracts")
    @permission_required("generate_purchase_contract")
    def purchase_contracts():
        latest_outputs = all_recent_outputs("*采购合同*.pdf") if can("manage_users") else user_recent_outputs("*采购合同*.pdf")
        return render_template(
            "purchase_contracts.html",
            default_contract_no=default_contract_no(user_file_label()),
            default_date=date.today().isoformat(),
            defaults={
                "buyer_name": DEFAULT_BUYER_NAME,
                "delivery_address": DEFAULT_DELIVERY_ADDRESS,
                "payment_terms": DEFAULT_PAYMENT_TERMS,
                "price_note": DEFAULT_PRICE_NOTE,
                "quality_terms": DEFAULT_QUALITY_TERMS,
            },
            latest_outputs=latest_outputs,
        )

    @app.get("/purchase-contracts/product-lookup")
    @permission_required("generate_purchase_contract")
    def purchase_contract_product_lookup():
        bld_no = request.args.get("bld", "").strip()
        if not bld_no:
            return jsonify({"found": False})
        bootstrap_from_excel(DB_PATH, CATALOG_PATH)
        with connect(DB_PATH) as conn:
            product = _product_by_bld(conn, bld_no)
        if not product:
            return jsonify({"found": False})
        image_url = product_image_url(product)
        thumb_url = product_image_thumb_url(product)
        return jsonify(
            {
                "found": True,
                "bld_no": product["bld_no"],
                "oe_no": product["oe_no_1"] or "",
                "product_name": product["item"] or "",
                "models": product["models"] or "",
                "image_url": image_url,
                "thumb_url": thumb_url or image_url,
            }
        )

    @app.post("/purchase-contracts/generate")
    @permission_required("generate_purchase_contract")
    def generate_purchase_contract():
        try:
            contract = purchase_contract_from_form(request.form)
            bootstrap_from_excel(DB_PATH, CATALOG_PATH)
            filename_stem = safe_filename_part(f"采购合同-{contract['contract_no']}", "purchase-contract")
            filename = f"{filename_stem}.pdf"
            output_path = unique_prefixed_path(user_output_dir(), filename)
            with connect(DB_PATH) as conn:
                _apply_catalog_values(conn, contract)
                generate_purchase_contract_pdf(contract, output_path)
                log_event(
                    conn,
                    "生成采购合同",
                    "purchase_contract",
                    output_path.name,
                    f"{contract['supplier_name']}，{len(contract['items'])} 行，合计 ¥{contract['total_amount']}",
                    actor=actor_name(),
                )
                conn.commit()
        except Exception as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("purchase_contracts"))

        return send_file(output_path, as_attachment=True, download_name=output_path.name)
