from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from flask import flash, redirect, render_template, request, send_file, url_for

from app.config import DATA_DIR
from app.helpers import user_file_label, user_output_dir, user_upload_dir, user_upload_path
from app.locks import ImportLockError
from app.security import actor_name, permission_required

from .factory import get_product_sync_service
from .sync_infrastructure import PACKAGE_SUFFIX
from .sync_service import ProductSyncApplyError


logger = logging.getLogger(__name__)


def _package_upload_path() -> Path | None:
    raw_path = request.form.get("package_path", "")
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    upload_root = user_upload_dir(create=False).resolve()
    if upload_root != path and upload_root not in path.parents:
        return None
    if not path.is_file() or not path.name.endswith(PACKAGE_SUFFIX):
        return None
    return path


def register(app) -> None:
    @app.get("/product-data-sync")
    @permission_required("sync_product_data")
    def product_data_sync():
        return render_template("product_data_sync.html", preview=None)

    @app.post("/product-data-sync/export")
    @permission_required("sync_product_data")
    def export_product_data_package():
        include_drawings = request.form.get("include_drawings") == "1"
        include_images = request.form.get("include_images") == "1"
        try:
            output_path = get_product_sync_service().export(
                output_dir=user_output_dir(),
                file_label=user_file_label(),
                include_drawings=include_drawings,
                include_images=include_images,
                actor=actor_name(),
            )
        except Exception:
            logger.exception("Product data package export failed")
            flash("产品数据包导出失败，请稍后重试。", "error")
            return redirect(url_for("product_data_sync"))
        return send_file(output_path, as_attachment=True)

    @app.post("/product-data-sync/import/preview")
    @permission_required("sync_product_data")
    def preview_product_data_package():
        file = request.files.get("package")
        if not file or not file.filename:
            flash("请选择产品数据包。", "error")
            return redirect(url_for("product_data_sync"))
        if not file.filename.endswith(PACKAGE_SUFFIX):
            flash("产品数据包必须是 .tar.gz 文件。", "error")
            return redirect(url_for("product_data_sync"))
        upload_path = user_upload_path(file.filename, prefix="product-data")
        file.save(upload_path)
        include_drawings = request.form.get("include_drawings") == "1"
        include_images = request.form.get("include_images") == "1"
        try:
            preview = get_product_sync_service().preview(
                upload_path,
                package_name=file.filename,
                include_drawings=include_drawings,
                include_images=include_images,
            )
        except ValueError as exc:
            flash(f"产品数据包读取失败：{exc}", "error")
            return redirect(url_for("product_data_sync"))
        except Exception:
            logger.exception("Product data package preview failed")
            flash("产品数据包读取失败，请检查文件后重试。", "error")
            return redirect(url_for("product_data_sync"))
        return render_template("product_data_sync.html", preview=preview)

    @app.post("/product-data-sync/import/apply")
    @permission_required("sync_product_data")
    def apply_product_data_package():
        package_path = _package_upload_path()
        if not package_path:
            flash("产品数据包路径无效，请重新上传预览。", "error")
            return redirect(url_for("product_data_sync"))
        backup_dir = DATA_DIR / "local-backups" / f"before-product-data-sync-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        try:
            result, _media_restored = get_product_sync_service().apply(
                package_path,
                backup_dir=backup_dir,
                include_drawings=request.form.get("include_drawings") == "1",
                include_images=request.form.get("include_images") == "1",
                deactivate_local_only=request.form.get("deactivate_local_only") == "1",
                actor=actor_name(),
            )
        except ImportLockError as exc:
            flash(str(exc), "error")
            return redirect(url_for("product_data_sync"))
        except ProductSyncApplyError as exc:
            logger.exception("Product data package apply failed")
            detail = "；已恢复本次媒体文件变更" if exc.media_restored else ""
            flash(f"产品数据包导入失败，请稍后重试{detail}。", "error")
            return redirect(url_for("product_data_sync"))
        flash(
            f"产品数据导入完成：新增 {result.new_count} 条，更新 {result.updated_count} 条，"
            f"跳过无变化 {result.unchanged_count} 条，跳过包内旧数据 {result.conflict_count} 条，"
            f"停用本机独有 {result.deactivated_count} 条。",
            "success",
        )
        return redirect(url_for("product_data_sync"))
