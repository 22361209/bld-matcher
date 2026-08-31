from __future__ import annotations

import logging
from typing import cast

from flask import Response, abort, flash, redirect, render_template, request, send_file, url_for

from app.drawings import product_drawing_path
from app.modules.products.factory import get_product_service
from app.modules.products.service import ProductNotFoundError
from app.product_media import resolve_product_image_path, resolve_product_image_thumb_path
from app.security import actor_name, permission_required


logger = logging.getLogger(__name__)

_MISSING_THUMBNAIL_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120" viewBox="0 0 160 120"><rect width="160" height="120" rx="8" fill="#eef1f4"/><path d="M45 84l22-24 15 15 11-12 22 21H45zm18-35a8 8 0 1 1 0-16 8 8 0 0 1 0 16z" fill="#a7b0ba"/></svg>"""


def register(app) -> None:
    @app.get("/products/drawings/batch")
    @permission_required("view_products")
    @permission_required("view_product_drawings")
    @permission_required("edit_products")
    def batch_drawings():
        return render_template("drawing_batch_placeholder.html")

    @app.get("/product-images/<path:name>")
    @permission_required("view_products")
    def product_image_data(name: str):
        path = resolve_product_image_path(name)
        if not path:
            flash("产品图片不存在。", "error")
            return redirect(url_for("products"))
        return send_file(path)

    @app.get("/product-image-thumbs/<path:name>")
    @permission_required("view_products")
    def product_image_thumb_data(name: str):
        path = resolve_product_image_thumb_path(name)
        if not path:
            response = Response(_MISSING_THUMBNAIL_SVG, mimetype="image/svg+xml")
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        return send_file(path)

    @app.post("/products/<int:product_id>/drawing")
    @permission_required("view_products")
    @permission_required("view_product_drawings")
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
    @permission_required("view_product_drawings")
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

    def _media_delete_target(product_id: int, *, embedded: bool) -> str:
        if embedded:
            return url_for("edit_product", product_id=product_id, embedded="1")
        return url_for("edit_product", product_id=product_id)

    @app.post("/products/<int:product_id>/images/<int:slot>/delete")
    @permission_required("view_products")
    @permission_required("edit_products")
    def delete_product_image(product_id: int, slot: int):
        if not 1 <= slot <= 5:
            abort(400)
        embedded = request.form.get("embedded") == "1"
        target = _media_delete_target(product_id, embedded=embedded)
        try:
            get_product_service().delete_image(product_id, slot, actor=actor_name())
        except LookupError:
            flash("产品不存在。", "error")
            return redirect(target)
        except Exception:
            logger.exception("Product image delete failed")
            flash("图片删除失败，请稍后重试。", "error")
            return redirect(target)
        flash(f"图片 {slot} 已删除，文件已移入归档目录保留。", "success")
        return redirect(target)

    @app.post("/products/<int:product_id>/drawing/delete")
    @permission_required("view_products")
    @permission_required("view_product_drawings")
    @permission_required("edit_products")
    def delete_product_drawing(product_id: int):
        embedded = request.form.get("embedded") == "1"
        target = _media_delete_target(product_id, embedded=embedded)
        try:
            get_product_service().delete_drawing(product_id, actor=actor_name())
        except LookupError:
            flash("产品不存在。", "error")
            return redirect(target)
        except Exception:
            logger.exception("Product drawing delete failed")
            flash("图纸删除失败，请稍后重试。", "error")
            return redirect(target)
        flash("图纸已删除，文件已移入归档目录保留。", "success")
        return redirect(target)
