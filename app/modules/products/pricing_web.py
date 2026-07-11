from __future__ import annotations

import logging
from pathlib import Path

from flask import flash, redirect, render_template, request, url_for

from app.helpers import user_upload_path
from app.locks import ImportLockError, import_lock
from app.modules.products.factory import get_product_service
from app.price_import import decode_rows, encode_rows
from app.security import actor_name, permission_required


logger = logging.getLogger(__name__)


def register(app) -> None:
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
