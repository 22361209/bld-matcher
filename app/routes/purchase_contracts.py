from __future__ import annotations

from datetime import date, datetime
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
    DEFAULT_SALES_PAYMENT_TERMS,
    DEFAULT_SALES_PRICE_NOTE,
    DEFAULT_SALES_QUALITY_TERMS,
    default_contract_no,
    default_sales_contract_no,
    generate_purchase_contract_pdf,
    generate_sales_contract_pdf,
    purchase_contract_from_form,
    sales_contract_from_form,
)
from app.security import actor_name, can, permission_required


CONTRACT_HISTORY_LIMIT = 200


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


def _operation_user(path: Path) -> str:
    try:
        first_part = path.resolve().relative_to(user_output_dir(create=False).resolve().parents[0]).parts[0]
    except (IndexError, OSError, ValueError):
        first_part = path.parent.name
    if first_part.startswith("u") and "-" in first_part:
        return first_part.split("-", 1)[1] or first_part
    return first_part


def _contract_party(path: Path, kind: str) -> str:
    parent = path.parent.name
    if parent.startswith("u") and "-" in parent:
        return ""
    if parent == kind:
        return ""
    return parent


def _contract_output_rows(paths: list[Path], kind: str, query: str) -> list[dict]:
    needle = query.strip().lower()
    rows = []
    for path in paths:
        party = _contract_party(path, kind)
        operator = _operation_user(path)
        stat = path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        haystack = " ".join([kind, path.name, party, operator, updated_at]).lower()
        if needle and needle not in haystack:
            continue
        rows.append(
            {
                "path": path,
                "kind": kind,
                "party": party,
                "name": path.name,
                "operator": operator,
                "updated_at": updated_at,
            }
        )
    return rows


def _collect_contract_outputs(output_reader, patterns: tuple[str, ...]) -> list[Path]:
    seen: set[Path] = set()
    paths: list[Path] = []
    for pattern in patterns:
        for path in output_reader(pattern, limit=CONTRACT_HISTORY_LIMIT):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
    return sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)


def _contract_history(output_reader) -> tuple[list[dict], dict[str, str]]:
    history_type = request.args.get("contract_type", "all")
    if history_type not in {"all", "purchase", "sales"}:
        history_type = "all"
    query = request.args.get("contract_q", "").strip()

    rows: list[dict] = []
    if history_type in {"all", "purchase"}:
        purchase_paths = _collect_contract_outputs(
            output_reader,
            ("采购合同/**/*.pdf",),
        )
        rows.extend(_contract_output_rows(purchase_paths, "采购合同", query))
    if history_type in {"all", "sales"}:
        sales_paths = _collect_contract_outputs(
            output_reader,
            ("销售合同/**/*.pdf",),
        )
        rows.extend(_contract_output_rows(sales_paths, "销售合同", query))

    rows = sorted(rows, key=lambda item: item["path"].stat().st_mtime, reverse=True)[:CONTRACT_HISTORY_LIMIT]
    return rows, {"contract_type": history_type, "contract_q": query}


def register(app) -> None:
    def _render_contract_management(contract_mode: str = "purchase"):
        is_sales = contract_mode == "sales"
        output_reader = all_recent_outputs if can("manage_users") else user_recent_outputs
        contract_outputs, contract_filters = _contract_history(output_reader)
        return render_template(
            "purchase_contracts.html",
            contract_mode=contract_mode,
            default_contract_no=default_sales_contract_no(user_file_label()) if is_sales else default_contract_no(user_file_label()),
            default_date=date.today().isoformat(),
            defaults={
                "buyer_name": DEFAULT_BUYER_NAME,
                "delivery_address": "" if is_sales else DEFAULT_DELIVERY_ADDRESS,
                "payment_terms": DEFAULT_SALES_PAYMENT_TERMS if is_sales else DEFAULT_PAYMENT_TERMS,
                "price_note": DEFAULT_SALES_PRICE_NOTE if is_sales else DEFAULT_PRICE_NOTE,
                "quality_terms": DEFAULT_SALES_QUALITY_TERMS if is_sales else DEFAULT_QUALITY_TERMS,
            },
            contract_outputs=contract_outputs,
            contract_filters=contract_filters,
        )

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
        return _render_contract_management("sales")

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
                "price_cny": product["price_cny"],
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
            supplier_folder = safe_filename_part(contract["supplier_name"], "supplier")
            filename_stem = safe_filename_part(
                f"{contract['contract_no']}{contract['supplier_name']}",
                "purchase-contract",
            )
            filename = f"{filename_stem}.pdf"
            output_path = unique_prefixed_path(user_output_dir() / "采购合同" / supplier_folder, filename)
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
            return redirect(url_for("contracts"))

        return send_file(output_path, as_attachment=True, download_name=output_path.name)

    @app.post("/sales-contracts/generate")
    @permission_required("generate_purchase_contract")
    def generate_sales_contract():
        try:
            contract = sales_contract_from_form(request.form)
            bootstrap_from_excel(DB_PATH, CATALOG_PATH)
            customer_folder = safe_filename_part(contract["customer_name"], "customer")
            filename_stem = safe_filename_part(
                f"{contract['contract_no']}{contract['customer_name']}",
                "sales-contract",
            )
            filename = f"{filename_stem}.pdf"
            output_path = unique_prefixed_path(user_output_dir() / "销售合同" / customer_folder, filename)
            with connect(DB_PATH) as conn:
                _apply_catalog_values(conn, contract)
                generate_sales_contract_pdf(contract, output_path)
                log_event(
                    conn,
                    "生成销售合同",
                    "sales_contract",
                    output_path.name,
                    f"{contract['customer_name']}，{len(contract['items'])} 行，合计 ¥{contract['total_amount']}",
                    actor=actor_name(),
                )
                conn.commit()
        except Exception as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("sales_contracts"))

        return send_file(output_path, as_attachment=True, download_name=output_path.name)
