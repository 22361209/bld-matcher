from __future__ import annotations

import logging
from datetime import datetime

from flask import Response, flash, jsonify, redirect, render_template, request, url_for

from app.config import DATA_DIR
from app.drawings import validate_product_drawing_file
from app.locks import ImportLockError, import_lock
from app.modules.products.brand_normalization import (
    BrandNormalizationConflictError,
    BrandNormalizationPreviewChangedError,
)
from app.modules.products.domain import validated_price_value
from app.modules.products.factory import get_product_service
from app.modules.products.service import ProductNotFoundError
from app.product_media import validate_product_image_file
from app.security import actor_name, permission_required, wants_json_response


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


def _brand_normalization_error(message: str, status: int):
    if wants_json_response():
        return jsonify({"ok": False, "error": message}), status
    flash(message, "error")
    return redirect(url_for("products"))


def register(app) -> None:
    @app.post("/products/brands/normalize")
    @permission_required("import_catalog")
    def normalize_product_brands():
        if request.form.get("confirmation") != "normalize-product-brands-v1":
            return _brand_normalization_error("缺少品牌清洗确认标记，未修改任何数据。", 400)
        expected_digest = request.form.get("snapshot_digest", "").strip()
        backup_path = (
            DATA_DIR
            / "local-backups"
            / f"before-product-brand-normalization-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.sqlite3"
        )
        try:
            actor = actor_name()
            with import_lock(actor, "产品品牌清洗"):
                result = get_product_service().normalize_brands(
                    backup_path=backup_path,
                    expected_digest=expected_digest,
                    actor=actor,
                )
        except ImportLockError as exc:
            return _brand_normalization_error(str(exc), 409)
        except BrandNormalizationPreviewChangedError as exc:
            return _brand_normalization_error(str(exc), 409)
        except BrandNormalizationConflictError as exc:
            return _brand_normalization_error(str(exc), 409)
        except ValueError as exc:
            return _brand_normalization_error(str(exc), 400)
        except Exception:
            logger.exception(
                "Product brand normalization failed",
                extra={"error_code": "product.brand_normalization_failed"},
            )
            return _brand_normalization_error(
                "品牌清洗失败，数据未修改，请稍后重试。",
                500,
            )
        if wants_json_response():
            return jsonify(
                {
                    "ok": True,
                    "changed_count": result.changed_count,
                    "backup": f"local-backups/{result.backup_path.name}",
                }
            )
        flash(f"产品品牌清洗完成，共规范 {result.changed_count} 条。", "success")
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
