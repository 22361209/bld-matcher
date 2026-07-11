from __future__ import annotations

import logging

from flask import Response, flash, redirect, render_template, request, url_for

from app.drawings import validate_product_drawing_file
from app.modules.products.domain import validated_price_value
from app.modules.products.factory import get_product_service
from app.modules.products.service import ProductNotFoundError
from app.product_media import validate_product_image_file
from app.security import actor_name, permission_required


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


def register(app) -> None:
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
