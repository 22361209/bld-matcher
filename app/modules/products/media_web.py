from __future__ import annotations

import logging
from typing import cast

from flask import flash, redirect, render_template, request, send_file, url_for

from app.drawings import product_drawing_path
from app.modules.products.factory import get_product_service
from app.modules.products.service import ProductNotFoundError
from app.product_media import resolve_product_image_path, resolve_product_image_thumb_path
from app.security import actor_name, login_required, permission_required


logger = logging.getLogger(__name__)


def register(app) -> None:
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
            download_name=cast(str | None, product["drawing_original_name"]) or path.name,
        )
