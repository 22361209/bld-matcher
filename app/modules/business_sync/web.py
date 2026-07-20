from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from flask import flash, redirect, render_template, request, send_file, url_for

from app.config import DATA_DIR
from app.helpers import user_file_label, user_output_dir, user_upload_dir, user_upload_path
from app.locks import ImportLockError, import_lock
from app.security import actor_name, permission_required

from .infrastructure import DATASETS, PACKAGE_SUFFIX, BusinessSyncRepository
from .service import BusinessSyncService


logger = logging.getLogger(__name__)


def _service() -> BusinessSyncService:
    from app.config import DB_PATH
    return BusinessSyncService(BusinessSyncRepository(DB_PATH))


def _selected() -> tuple[str, ...]:
    return tuple(key for key in DATASETS if key in request.form.getlist("dataset"))


def _uploaded_path() -> Path | None:
    raw_path = request.form.get("package_path", "")
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    root = user_upload_dir(create=False).resolve()
    return path if root in path.parents and path.is_file() and path.name.endswith(PACKAGE_SUFFIX) else None


def _selected_conflicts() -> dict[str, set[str]]:
    selected = {key: set() for key in DATASETS}
    for value in request.form.getlist("use_package"):
        key, separator, identity = value.partition(":")
        if separator and key in DATASETS and identity:
            selected[key].add(identity)
    return selected


def register(app) -> None:
    @app.get("/business-data-sync")
    @permission_required("sync_product_data")
    def business_data_sync():
        return render_template("business_data_sync.html", datasets=DATASETS, preview=None)

    @app.post("/business-data-sync/export")
    @permission_required("sync_product_data")
    def export_business_data_sync():
        selected = _selected()
        if not selected:
            flash("请至少选择一类业务数据。", "error")
            return redirect(url_for("business_data_sync"))
        path = user_output_dir() / f"business-data-{user_file_label()}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{PACKAGE_SUFFIX}"
        try:
            return send_file(_service().export(output_path=path, selected=selected, actor=actor_name()), as_attachment=True)
        except Exception:
            logger.exception("Business data package export failed")
            flash("业务数据包导出失败。", "error")
            return redirect(url_for("business_data_sync"))

    @app.post("/business-data-sync/preview")
    @permission_required("sync_product_data")
    def preview_business_data_sync():
        file = request.files.get("package")
        if not file or not file.filename or not file.filename.endswith(PACKAGE_SUFFIX):
            flash("请选择 .tar.gz 业务数据包。", "error")
            return redirect(url_for("business_data_sync"))
        path = user_upload_path(file.filename, prefix="business-data")
        file.save(path)
        try:
            preview = _service().preview(path)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("business_data_sync"))
        except Exception:
            logger.exception("Business data package preview failed")
            flash("业务数据包读取失败，请检查文件后重试。", "error")
            return redirect(url_for("business_data_sync"))
        return render_template("business_data_sync.html", datasets=DATASETS, preview={**preview, "package_path": str(path)})

    @app.post("/business-data-sync/apply")
    @permission_required("sync_product_data")
    def apply_business_data_sync():
        path = _uploaded_path()
        if path is None:
            flash("数据包路径无效，请重新上传预览。", "error")
            return redirect(url_for("business_data_sync"))
        try:
            actor = actor_name()
            with import_lock(actor, "业务数据包导入"):
                result = _service().apply(
                    path,
                    backup_path=DATA_DIR / "local-backups" / f"before-business-sync-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.sqlite3",
                    actor=actor,
                    expected_token=request.form.get("preview_token", ""),
                    selected_conflicts=_selected_conflicts(),
                )
        except ImportLockError as exc:
            flash(str(exc), "error")
            return redirect(url_for("business_data_sync"))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("business_data_sync"))
        except Exception:
            logger.exception("Business data package apply failed")
            flash("业务数据包导入失败，已保留导入前备份。", "error")
            return redirect(url_for("business_data_sync"))
        flash("业务数据导入完成：" + "；".join(f"{DATASETS[key][2]}新增 {value['new']}、更新 {value['updated']}、冲突 {value['conflict']}" for key, value in result.items()), "success")
        return redirect(url_for("business_data_sync"))
