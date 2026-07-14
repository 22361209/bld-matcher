from __future__ import annotations

import logging
import shutil
from datetime import datetime
from math import ceil
from typing import Any

from flask import flash, redirect, render_template, request, send_file, url_for

from app.config import CATALOG_PATH, DATA_DIR
from app.helpers import unique_prefixed_path, user_file_label, user_output_dir, user_upload_path
from app.locks import ImportLockError, import_lock
from app.modules.products.domain import (
    ProductFilters,
    ProductFilterValidationError,
    build_product_filters,
)
from app.modules.products.factory import get_product_service
from app.security import actor_name, can, login_required, permission_required


PRODUCT_PAGE_SIZE = 50
logger = logging.getLogger(__name__)


def _product_filters(values: Any, *, default_status: str) -> ProductFilters:
    return build_product_filters(
        {
            "q": values.get("q", ""),
            "bld": values.get("bld", ""),
            "oe": values.get("oe", ""),
            "status": values.get("status", default_status),
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


def _product_page_url(filters: ProductFilters, page: int) -> str:
    params: dict[str, Any] = {}
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
        "links": links,
    }


def register(app) -> None:
    @app.post("/catalog")
    @permission_required("import_catalog")
    def upload_catalog():
        redirect_target = url_for("products") if request.form.get("next") == "products" else url_for("index")
        file = request.files.get("catalog")
        if not file or not file.filename:
            flash("请选择产品目录 Excel 文件。", "error")
            return redirect(redirect_target)
        if not file.filename.lower().endswith(".xlsx"):
            flash("产品目录请使用 .xlsx 文件。", "error")
            return redirect(redirect_target)
        upload_path = user_upload_path(file.filename, prefix="catalog")
        file.save(upload_path)

        try:
            with import_lock(actor_name(), "产品目录导入"):
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                backup = DATA_DIR / f"catalog-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
                if CATALOG_PATH.exists():
                    shutil.copy2(CATALOG_PATH, backup)
                shutil.copy2(upload_path, CATALOG_PATH)
                try:
                    get_product_service().import_catalog(CATALOG_PATH, actor=actor_name())
                except Exception:
                    if backup.exists():
                        shutil.copy2(backup, CATALOG_PATH)
                    raise
        except ImportLockError as exc:
            flash(str(exc), "error")
            return redirect(redirect_target)
        except Exception:
            logger.exception("Catalog import failed and the previous catalog was restored")
            flash("目录读取失败，已恢复旧目录。", "error")
            return redirect(redirect_target)

        flash("产品目录已导入。已有 BLD NO. 会更新，新增 BLD NO. 会加入产品库。", "success")
        return redirect(redirect_target)

    @app.get("/products")
    @login_required
    def products():
        try:
            filters = _product_query_args()
        except ProductFilterValidationError as exc:
            return f"筛选条件无效：{exc}", 400, {"Content-Type": "text/plain; charset=utf-8"}
        service = get_product_service()
        requested_page = _request_page()
        page = service.search(
            filters,
            limit=PRODUCT_PAGE_SIZE,
            offset=(requested_page - 1) * PRODUCT_PAGE_SIZE,
        )
        pagination = _product_pagination(filters, requested_page, page.total)
        if int(pagination["page"]) != requested_page:
            page = service.search(
                filters,
                limit=PRODUCT_PAGE_SIZE,
                offset=(int(pagination["page"]) - 1) * PRODUCT_PAGE_SIZE,
            )
        rows = [record.web_payload() for record in page.records]
        stats = service.stats().as_dict()
        filter_options = service.filter_options(filters).web_payload()
        brand_normalization_preview = service.preview_brand_normalization() if can("import_catalog") else None
        return render_template(
            "products.html",
            products=rows,
            total_products=page.total,
            product_page_size=PRODUCT_PAGE_SIZE,
            pagination=pagination,
            query=filters.query,
            bld_query=filters.bld_query or filters.query,
            oe_query=filters.oe_query,
            status=filters.status,
            filter_options=filter_options,
            brand_normalization_preview=brand_normalization_preview,
            column_filters={
                "brand": [*filters.brands, *(("",) if filters.brand_blank else ())],
                "item": [*filters.items, *(("",) if filters.item_blank else ())],
                "product_status": [
                    *filters.product_statuses,
                    *(("",) if filters.product_status_blank else ()),
                ],
            },
            stats=stats,
        )

    @app.get("/products/export")
    @permission_required("export_catalog")
    def export_products_options():
        status = request.args.get("status", "all")
        return render_template("export_catalog.html", status=status)

    @app.post("/products/export")
    @permission_required("export_catalog")
    def export_products():
        try:
            filters = _product_filters(request.form, default_status="all")
        except ProductFilterValidationError as exc:
            return f"筛选条件无效：{exc}", 400, {"Content-Type": "text/plain; charset=utf-8"}
        export_format = request.form.get("export_format", "bld")
        if export_format not in {"bld", "brand"}:
            export_format = "bld"
        format_label = "brand" if export_format == "brand" else "bld"
        output_path = unique_prefixed_path(
            user_output_dir(),
            f"catalog-export-{format_label}-{user_file_label()}-{datetime.now().strftime('%y%m%d')}.xlsx",
        )
        exported = get_product_service().export_catalog(
            output_path,
            filters=filters,
            export_format=export_format,
            actor=actor_name(),
        )
        if not exported:
            flash("当前筛选条件下没有可导出的产品。", "error")
            return redirect(_product_page_url(filters, 1))
        return send_file(output_path, as_attachment=True)
