from __future__ import annotations

import logging
from math import ceil
from pathlib import Path
from typing import Any, cast

from flask import abort, flash, redirect, render_template, request, send_file, url_for

from app.helpers import (
    all_recent_outputs,
    clean_original_filename,
    user_file_label,
    user_output_dir,
    user_recent_outputs,
    user_upload_path,
)
from app.security import actor_name, can, login_required, permission_required

from .factory import get_material_service
from .infrastructure import MaterialImportBusyError


logger = logging.getLogger(__name__)
MATERIAL_PAGE_SIZE = 50
MATERIAL_HISTORY_LIMIT = 500


def _request_page() -> int:
    try:
        return max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        return 1


def _material_page_url(query: str, status: str, page: int) -> str:
    params: dict[str, object] = {}
    if query.strip():
        params["q"] = query
    if status != "active":
        params["status"] = status
    if page > 1:
        params["page"] = page
    return f"{url_for('material_items', **cast(Any, params))}#materials-results"


def _material_pagination(query: str, status: str, page: int, total: int) -> dict[str, object]:
    total_pages = max(1, ceil(total / MATERIAL_PAGE_SIZE))
    page = min(max(1, page), total_pages)
    start = ((page - 1) * MATERIAL_PAGE_SIZE) + 1 if total else 0
    end = min(total, page * MATERIAL_PAGE_SIZE)
    window = {1, total_pages, page - 1, page, page + 1}
    pages = sorted(item for item in window if 1 <= item <= total_pages)
    links = []
    previous_page = 0
    for item in pages:
        if previous_page and item - previous_page > 1:
            links.append({"gap": True})
        links.append({"page": item, "url": _material_page_url(query, status, item), "current": item == page})
        previous_page = item
    return {
        "page": page,
        "total_pages": total_pages,
        "start": start,
        "end": end,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": _material_page_url(query, status, page - 1) if page > 1 else "",
        "next_url": _material_page_url(query, status, page + 1) if page < total_pages else "",
        "links": links,
    }


def register(app) -> None:
    @app.get("/materials")
    @login_required
    def materials():
        service = get_material_service()
        material_history_query = request.args.get("material_history_q", "").strip()
        output_reader = all_recent_outputs if can("manage_users") else user_recent_outputs
        material_history_files = service.history_rows(
            output_reader("*料单*.xlsx", limit=MATERIAL_HISTORY_LIMIT),
            material_history_query,
        )
        return render_template(
            "materials.html",
            show_material_items=False,
            material_file_stats=service.source_stats(),
            material_stats=service.stats(),
            material_history_files=material_history_files,
            material_history_query=material_history_query,
        )

    @app.get("/materials/items")
    @login_required
    def material_items():
        service = get_material_service()
        query = request.args.get("q", "")
        status = request.args.get("status", "active")
        first_page = service.list_items(query=query, status=status, limit=MATERIAL_PAGE_SIZE, offset=0)
        pagination = _material_pagination(query, status, _request_page(), first_page.total)
        page_number = int(cast(Any, pagination["page"]))
        page = (
            first_page
            if page_number == 1
            else service.list_items(
                query=query,
                status=status,
                limit=MATERIAL_PAGE_SIZE,
                offset=(page_number - 1) * MATERIAL_PAGE_SIZE,
            )
        )
        return render_template(
            "materials.html",
            show_material_items=True,
            material_file_stats=service.source_stats(),
            material_stats=page.stats,
            material_items=page.records,
            total_material_items=page.total,
            material_page_size=MATERIAL_PAGE_SIZE,
            pagination=pagination,
            query=query,
            status=status,
        )

    @app.get("/materials/template")
    @login_required
    def download_material_template():
        path = get_material_service().create_template()
        return send_file(path, as_attachment=True, download_name="生产计划模板.xlsx")

    @app.post("/materials/generate")
    @permission_required("generate_material_sheet")
    def generate_materials():
        file = request.files.get("plan")
        if not file or not file.filename:
            flash("请选择生产计划 Excel 文件。", "error")
            return redirect(url_for("materials"))
        if Path(file.filename).suffix.lower() != ".xlsx":
            flash("生产计划请使用 .xlsx 文件。", "error")
            return redirect(url_for("materials"))
        upload_path = user_upload_path(file.filename, prefix="material-plan")
        file.save(upload_path)
        try:
            output_path, _summary = get_material_service().generate_sheet(
                upload_path,
                user_output_dir(),
                filename_prefix=f"{user_file_label()}-",
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("materials"))
        except Exception:
            logger.exception("Material sheet generation failed")
            flash("生成失败，请稍后重试。", "error")
            return redirect(url_for("materials"))
        return send_file(output_path, as_attachment=True)

    @app.post("/materials/data")
    @permission_required("manage_materials")
    def upload_material_data():
        file = request.files.get("material_data")
        if not file or not file.filename:
            flash("请选择材料数据 Excel 文件。", "error")
            return redirect(url_for("material_items"))
        if Path(file.filename).suffix.lower() != ".xlsx":
            flash("材料数据请使用 .xlsx 文件。", "error")
            return redirect(url_for("material_items"))
        upload_path = user_upload_path(file.filename, prefix="material-data")
        file.save(upload_path)
        try:
            get_material_service().import_data(
                upload_path,
                original_name=clean_original_filename(file.filename, fallback_suffix=".xlsx"),
                actor=actor_name(),
            )
        except MaterialImportBusyError as exc:
            flash(str(exc), "error")
            return redirect(url_for("material_items"))
        except ValueError as exc:
            flash(f"材料数据读取失败：{exc}", "error")
            return redirect(url_for("material_items"))
        except Exception:
            logger.exception("Material data import failed")
            flash("材料数据导入失败，请稍后重试。", "error")
            return redirect(url_for("material_items"))
        flash("材料数据已更新并导入数据库。", "success")
        return redirect(url_for("material_items"))

    @app.get("/materials/items/new")
    @permission_required("manage_materials")
    def new_material_item():
        return render_template("material_item_form.html", item=None)

    @app.get("/materials/items/<int:item_id>/edit")
    @permission_required("manage_materials")
    def edit_material_item(item_id: int):
        item = get_material_service().get_item(item_id)
        if not item:
            flash("材料明细不存在。", "error")
            return redirect(url_for("material_items"))
        return render_template("material_item_form.html", item=item)

    @app.post("/materials/items/save")
    @permission_required("manage_materials")
    def save_material_item():
        data = {
            "id": request.form.get("id", ""),
            "model": request.form.get("model", ""),
            "code": request.form.get("code", ""),
            "category": request.form.get("category", ""),
            "car": request.form.get("car", ""),
            "part": request.form.get("part", ""),
            "spec_text": request.form.get("spec_text", ""),
            "pieces": request.form.get("pieces", ""),
            "thickness": request.form.get("thickness", ""),
            "width": request.form.get("width", ""),
            "length": request.form.get("length", ""),
            "active": request.form.get("active", "0"),
        }
        try:
            get_material_service().save_item(data, actor=actor_name())
        except ValueError as exc:
            flash(f"保存失败：{exc}", "error")
            return redirect(url_for("material_items"))
        except Exception:
            logger.exception("Material item save failed")
            flash("保存失败，请稍后重试。", "error")
            return redirect(url_for("material_items"))
        flash("材料明细已保存。", "success")
        return redirect(url_for("material_items", q=data["model"]))

    @app.post("/materials/items/<int:item_id>/deactivate")
    @permission_required("manage_materials")
    def stop_material_item(item_id: int):
        get_material_service().deactivate_item(item_id, actor=actor_name())
        flash("材料明细已停用。", "success")
        return redirect(url_for("material_items"))

    @app.get("/material-drawings")
    @login_required
    def material_drawings():
        context = get_material_service().drawing_page(
            query=request.args.get("q", ""),
            category=request.args.get("category", ""),
            selected_name=request.args.get("selected", ""),
        )
        return render_template("material_drawings.html", **context)

    @app.post("/material-drawings/upload")
    @permission_required("manage_materials")
    def upload_material_drawing():
        file = request.files.get("drawing")
        if not file or not file.filename:
            flash("请选择物料图纸 PDF 文件。", "error")
            return redirect(url_for("material_drawings"))
        try:
            get_material_service().upload_drawing(file, actor=actor_name())
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("material_drawings"))
        except Exception:
            logger.exception("Material drawing upload failed")
            flash("物料图纸上传失败，请稍后重试。", "error")
            return redirect(url_for("material_drawings"))
        flash("物料图纸已上传。", "success")
        return redirect(url_for("material_drawings"))

    @app.get("/material-drawings/preview/<path:name>")
    @login_required
    def preview_material_drawing(name: str):
        path = get_material_service().drawing_path(name)
        if path is None:
            abort(404)
        return send_file(path, as_attachment=False, mimetype="application/pdf", download_name=path.name)

    @app.get("/material-drawings/<path:name>")
    @login_required
    def download_material_drawing(name: str):
        path = get_material_service().drawing_path(name)
        if path is None:
            abort(404)
        return send_file(path, as_attachment=True, download_name=path.name)
