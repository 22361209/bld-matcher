from __future__ import annotations

import logging
import shutil
from datetime import datetime
from math import ceil
from pathlib import Path

from flask import Response, flash, redirect, render_template, request, send_file, url_for

from app.config import CATALOG_PATH, DATA_DIR
from app.drawings import product_drawing_path, validate_product_drawing_file
from app.helpers import unique_prefixed_path, user_file_label, user_output_dir, user_upload_path
from app.locks import ImportLockError, import_lock
from app.modules.products.domain import validated_price_value
from app.modules.products.factory import get_product_service
from app.modules.products.service import ProductNotFoundError
from app.price_import import decode_rows, encode_rows
from app.product_media import resolve_product_image_path, resolve_product_image_thumb_path, validate_product_image_file
from app.security import actor_name, login_required, permission_required


PRODUCT_PAGE_SIZE = 50
logger = logging.getLogger(__name__)


def _pending_product_images() -> list[tuple[int, object]]:
    files = []
    for image_slot in range(1, 6):
        image_file = request.files.get(f"product_image_{image_slot}")
        if not image_file and image_slot == 1:
            image_file = request.files.get("product_image")
        if image_file and image_file.filename:
            validate_product_image_file(image_file)
            files.append((image_slot, image_file))
    return files


def _product_query_args() -> dict[str, object]:
    query = request.args.get("q", "")
    bld_query = request.args.get("bld", "")
    oe_query = request.args.get("oe", "")
    if oe_query.strip():
        bld_query = ""
    status = request.args.get("status", "active")
    if status not in {"active", "all", "inactive"}:
        status = "active"
    return {
        "query": query,
        "bld_query": bld_query,
        "oe_query": oe_query,
        "status": status,
        "include_inactive": status == "all",
        "only_inactive": status == "inactive",
    }


def _request_page() -> int:
    try:
        return max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        return 1


def _product_page_url(filters: dict[str, object], page: int) -> str:
    params: dict[str, object] = {}
    if str(filters["oe_query"]).strip():
        params["oe"] = filters["oe_query"]
    elif str(filters["bld_query"]).strip():
        params["bld"] = filters["bld_query"]
    elif str(filters["query"]).strip():
        params["q"] = filters["query"]
    if str(filters["status"]) != "active":
        params["status"] = filters["status"]
    if page > 1:
        params["page"] = page
    return f"{url_for('products', **params)}#products-results"


def _product_pagination(filters: dict[str, object], page: int, total: int) -> dict[str, object]:
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


def _embedded_product_done_response() -> Response:
    fallback = url_for("products")
    return Response(
        f"""<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>产品已保存</title></head>
  <body>
    <script>
      if (window.parent && window.parent !== window) {{
        window.parent.location.reload();
      }} else {{
        window.location.href = {fallback!r};
      }}
    </script>
  </body>
</html>""",
        mimetype="text/html",
    )


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
        filters = _product_query_args()
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
        return render_template(
            "products.html",
            products=rows,
            total_products=page.total,
            product_page_size=PRODUCT_PAGE_SIZE,
            pagination=pagination,
            query=str(filters["query"]),
            bld_query=str(filters["bld_query"] or filters["query"]),
            oe_query=str(filters["oe_query"]),
            status=str(filters["status"]),
            stats=stats,
        )

    @app.get("/products/export")
    @permission_required("export_catalog")
    def export_products_options():
        status = request.args.get("status", "all")
        return render_template("export_catalog.html", status=status)

    @app.get("/products/drawings/batch")
    @permission_required("edit_products")
    def batch_drawings():
        return render_template("drawing_batch_placeholder.html")

    @app.get("/product-images/<path:name>")
    @login_required
    def product_image_data(name: str):
        path = resolve_product_image_path(name)
        if not path:
            flash("产品图片不存在。", "error")
            return redirect(url_for("products"))
        return send_file(path)

    @app.get("/product-image-thumbs/<path:name>")
    @login_required
    def product_image_thumb_data(name: str):
        path = resolve_product_image_thumb_path(name)
        if not path:
            flash("产品图片不存在。", "error")
            return redirect(url_for("products"))
        return send_file(path)

    @app.post("/products/export")
    @permission_required("export_catalog")
    def export_products():
        status = request.form.get("status", "all")
        include_inactive = status != "active"
        export_format = request.form.get("export_format", "bld")
        if export_format not in {"bld", "brand"}:
            export_format = "bld"
        format_label = "brand" if export_format == "brand" else "bld"
        output_path = unique_prefixed_path(
            user_output_dir(),
            f"catalog-export-{format_label}-{user_file_label()}-{datetime.now().strftime('%y%m%d')}.xlsx",
        )
        get_product_service().export_catalog(
            output_path,
            include_inactive=include_inactive,
            export_format=export_format,
            actor=actor_name(),
        )
        return send_file(output_path, as_attachment=True)

    @app.get("/prices/import")
    @permission_required("edit_products")
    def price_import():
        return render_template("price_import.html", preview=None)

    @app.post("/prices/import/preview")
    @permission_required("edit_products")
    def price_import_preview():
        file = request.files.get("price_file")
        if not file or not file.filename:
            flash("请选择单价 Excel 文件。", "error")
            return redirect(url_for("price_import"))
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {".xls", ".xlsx"}:
            flash("单价导入文件支持 .xls 和 .xlsx。", "error")
            return redirect(url_for("price_import"))

        upload_path = user_upload_path(file.filename, prefix="price")
        file.save(upload_path)
        try:
            preview = get_product_service().preview_prices(upload_path)
        except ValueError as exc:
            flash(f"解析失败：{exc}", "error")
            return redirect(url_for("price_import"))
        except Exception:
            logger.exception("Price import preview failed")
            flash("解析失败，请检查文件后重试。", "error")
            return redirect(url_for("price_import"))
        return render_template("price_import.html", preview=preview, payload=encode_rows(preview["rows"]))

    @app.post("/prices/import/apply")
    @permission_required("edit_products")
    def price_import_apply():
        try:
            rows = decode_rows(request.form.get("payload", "[]"))
        except Exception:
            logger.exception("Price import payload could not be decoded")
            flash("导入数据无效，请重新预览。", "error")
            return redirect(url_for("price_import"))

        try:
            with import_lock(actor_name(), "单价批量导入"):
                updated, skipped = get_product_service().apply_prices(rows, actor=actor_name())
        except ImportLockError as exc:
            flash(str(exc), "error")
            return redirect(url_for("price_import"))
        flash(f"单价导入完成：更新 {updated} 条，跳过 {skipped} 条。", "success")
        return redirect(url_for("products"))

    @app.get("/products/new")
    @permission_required("edit_products")
    def new_product():
        return render_template("product_form.html", product=None)

    @app.get("/products/<int:product_id>/edit")
    @permission_required("edit_products")
    def edit_product(product_id: int):
        try:
            product = get_product_service().get(product_id).web_payload()
        except ProductNotFoundError:
            flash("产品不存在。", "error")
            return redirect(url_for("products"))
        return render_template("product_form.html", product=product, embedded=request.args.get("embedded") == "1")

    @app.post("/products/save")
    @permission_required("edit_products")
    def save_product():
        try:
            image_files = _pending_product_images()
            drawing_file = request.files.get("drawing")
            if drawing_file and drawing_file.filename:
                validate_product_drawing_file(drawing_file)
            data = {
                "bld_no": request.form.get("bld_no", ""),
                "series": request.form.get("series", ""),
                "item": request.form.get("item", ""),
                "oe_no_1": request.form.get("oe_no_1", ""),
                "oe_no_2": request.form.get("oe_no_2", ""),
                "models": request.form.get("models", ""),
                "price_cny": validated_price_value(request.form.get("price_cny", "")),
                "product_status": request.form.get("product_status", ""),
                "image_path": request.form.get("image_path", ""),
                "active": request.form.get("active", "0"),
            }
            get_product_service().save(
                data,
                actor=actor_name(),
                image_files=image_files,
                drawing_file=drawing_file if drawing_file and drawing_file.filename else None,
            )
        except ValueError as exc:
            flash(f"保存失败：{exc}", "error")
            if request.form.get("embedded") == "1":
                return _embedded_product_done_response()
            return redirect(url_for("products"))
        except Exception:
            logger.exception("Product save failed")
            flash("保存失败，请稍后重试。", "error")
            if request.form.get("embedded") == "1":
                return _embedded_product_done_response()
            return redirect(url_for("products"))
        flash("产品已保存。", "success")
        if request.form.get("embedded") == "1":
            return _embedded_product_done_response()
        return redirect(url_for("products", q=data["bld_no"]))

    @app.post("/products/<int:product_id>/drawing")
    @permission_required("edit_products")
    def upload_product_drawing(product_id: int):
        file = request.files.get("drawing")
        if not file or not file.filename:
            flash("请选择 PDF 图纸文件。", "error")
            return redirect(url_for("products") + "#products-results")
        try:
            product = get_product_service().save_drawing(
                product_id,
                file,
                actor=actor_name(),
            ).web_payload()
        except ProductNotFoundError:
            flash("产品不存在。", "error")
            return redirect(url_for("products") + "#products-results")
        except ValueError as exc:
            flash(f"图纸上传失败：{exc}", "error")
            return redirect(url_for("products") + "#products-results")
        except Exception:
            logger.exception("Product drawing upload failed")
            flash("图纸上传失败，请稍后重试。", "error")
            return redirect(url_for("products") + "#products-results")

        flash("图纸已保存。", "success")
        return redirect(url_for("products", bld=product["bld_no"]) + "#products-results")

    @app.get("/products/<int:product_id>/drawing")
    @login_required
    def product_drawing(product_id: int):
        try:
            product = get_product_service().get(product_id).web_payload()
        except ProductNotFoundError:
            flash("产品不存在。", "error")
            return redirect(url_for("products"))
        path = product_drawing_path(product)
        if not path:
            flash("这个产品还没有 PDF 图纸。", "error")
            return redirect(url_for("products", bld=product["bld_no"]) + "#products-results")
        download = request.args.get("download") == "1"
        return send_file(
            path,
            mimetype="application/pdf",
            as_attachment=download,
            download_name=product["drawing_original_name"] or path.name,
        )

    @app.post("/products/<int:product_id>/deactivate")
    @permission_required("edit_products")
    def stop_product(product_id: int):
        try:
            get_product_service().deactivate(product_id, actor=actor_name())
        except ProductNotFoundError:
            flash("产品不存在。", "error")
            return redirect(url_for("products"))
        flash("产品已停用，历史资料仍保留。", "success")
        return redirect(url_for("products"))

    @app.post("/products/<int:product_id>/delete")
    @permission_required("edit_products")
    def remove_product(product_id: int):
        product_record = get_product_service().delete(product_id, actor=actor_name())
        product = product_record.web_payload() if product_record else None
        if not product:
            flash("产品不存在或已经删除。", "error")
            if request.form.get("embedded") == "1":
                return _embedded_product_done_response()
            return redirect(url_for("products"))
        flash(f"产品 {product['bld_no']} 已删除。", "success")
        if request.form.get("embedded") == "1":
            return _embedded_product_done_response()
        return redirect(url_for("products"))
