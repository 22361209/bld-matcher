from __future__ import annotations

import shutil
from datetime import datetime
from functools import lru_cache
from math import ceil
from pathlib import Path

from flask import flash, redirect, render_template, request, send_file, url_for

from app.config import DATA_DIR, DB_PATH, MATERIAL_DATA_PATH, MATERIAL_TEMPLATE_PATH
from app.database import (
    bootstrap_materials_from_excel,
    connect,
    count_material_items,
    deactivate_material_item,
    get_material_item,
    import_materials_from_excel,
    list_material_items,
    log_event,
    material_item_stats,
    rows_for_material_sheet,
    upsert_material_item,
)
from app.helpers import all_recent_outputs, clean_original_filename, user_file_label, user_output_dir, user_recent_outputs, user_upload_path
from app.locks import ImportLockError, import_lock
from app.material_sheet import (
    create_plan_template,
    generate_material_sheet_from_materials,
    material_data_stats,
    sync_material_specs_from_dimensions,
)
from app.security import actor_name, can, login_required, permission_required


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
    return f"{url_for('material_items', **params)}#materials-results"


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


def _file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=16)
def _cached_material_data_stats(path_text: str, signature: tuple[int, int]) -> dict:
    return material_data_stats(Path(path_text))


def _material_data_stats(path: Path) -> dict:
    return _cached_material_data_stats(str(path), _file_signature(path))


def _operation_user(path: Path) -> str:
    parent = path.parent.name
    if not parent.startswith("u") or "-" not in parent:
        return "历史文件"
    return parent.split("-", 1)[1] or parent


def _material_history_rows(paths: list[Path], query: str) -> list[dict]:
    needle = query.strip().lower()
    rows = []
    for path in paths:
        operator = _operation_user(path)
        stat = path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        haystack = " ".join([path.name, path.suffix.lower().lstrip(".").upper(), operator, updated_at]).lower()
        if needle and needle not in haystack:
            continue
        rows.append(
            {
                "path": path,
                "name": path.name,
                "kind": path.suffix.lower().lstrip(".").upper(),
                "operator": operator,
                "updated_at": updated_at,
            }
        )
    return rows


def register(app) -> None:
    @app.get("/materials")
    @login_required
    def materials():
        bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH)
        with connect(DB_PATH) as conn:
            stats = material_item_stats(conn)
        material_history_query = request.args.get("material_history_q", "").strip()
        output_reader = all_recent_outputs if can("manage_users") else user_recent_outputs
        material_history_files = _material_history_rows(
            output_reader("*料单*.xlsx", limit=MATERIAL_HISTORY_LIMIT),
            material_history_query,
        )
        return render_template(
            "materials.html",
            show_material_items=False,
            material_file_stats=_material_data_stats(MATERIAL_DATA_PATH),
            material_stats=stats,
            material_path=MATERIAL_DATA_PATH if MATERIAL_DATA_PATH.exists() else None,
            material_history_files=material_history_files,
            material_history_query=material_history_query,
        )

    @app.get("/materials/items")
    @login_required
    def material_items():
        bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH)
        query = request.args.get("q", "")
        status = request.args.get("status", "active")
        if status not in {"active", "all", "inactive"}:
            status = "active"
        with connect(DB_PATH) as conn:
            total_items = count_material_items(
                conn,
                query=query,
                include_inactive=status == "all",
                only_inactive=status == "inactive",
            )
            pagination = _material_pagination(query, status, _request_page(), total_items)
            items = list_material_items(
                conn,
                query=query,
                include_inactive=status == "all",
                only_inactive=status == "inactive",
                limit=MATERIAL_PAGE_SIZE,
                offset=(int(pagination["page"]) - 1) * MATERIAL_PAGE_SIZE,
            )
            stats = material_item_stats(conn)
        return render_template(
            "materials.html",
            show_material_items=True,
            material_file_stats=_material_data_stats(MATERIAL_DATA_PATH),
            material_stats=stats,
            material_items=items,
            total_material_items=total_items,
            material_page_size=MATERIAL_PAGE_SIZE,
            pagination=pagination,
            material_path=MATERIAL_DATA_PATH if MATERIAL_DATA_PATH.exists() else None,
            query=query,
            status=status,
        )

    @app.get("/materials/template")
    @login_required
    def download_material_template():
        create_plan_template(MATERIAL_TEMPLATE_PATH)
        return send_file(MATERIAL_TEMPLATE_PATH, as_attachment=True, download_name="生产计划模板.xlsx")

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
            bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH)
            with connect(DB_PATH) as conn:
                material_rows = rows_for_material_sheet(conn)
            if not material_rows:
                raise ValueError("还没有可用的材料明细，请先上传或新增材料数据。")
            output_path, summary = generate_material_sheet_from_materials(
                material_rows,
                upload_path,
                user_output_dir(),
                filename_prefix=f"{user_file_label()}-",
            )
            with connect(DB_PATH) as conn:
                missing_text = f"，未匹配 {len(summary['missing'])} 个型号" if summary["missing"] else ""
                log_event(
                    conn,
                    "生成生产料单",
                    "material_sheet",
                    output_path.name,
                    f"生产计划 {summary['plan_count']} 行，料单明细 {summary['detail_count']} 行，规格 {summary['spec_count']} 个{missing_text}",
                    actor=actor_name(),
                )
                conn.commit()
        except Exception as exc:
            flash(f"生成失败：{exc}", "error")
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

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = user_upload_path(file.filename, prefix="material-data")
        file.save(upload_path)
        stats = material_data_stats(upload_path)
        if stats.get("invalid"):
            flash(f"材料数据读取失败：{stats.get('error') or '文件里必须包含“材料数据”工作表。'}", "error")
            return redirect(url_for("material_items"))

        try:
            with import_lock(actor_name(), "材料数据导入"):
                if MATERIAL_DATA_PATH.exists():
                    backup = DATA_DIR / f"stamping_materials-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
                    shutil.copy2(MATERIAL_DATA_PATH, backup)
                shutil.copy2(upload_path, MATERIAL_DATA_PATH)
                normalized = sync_material_specs_from_dimensions(MATERIAL_DATA_PATH)
                with connect(DB_PATH) as conn:
                    imported = import_materials_from_excel(conn, MATERIAL_DATA_PATH, replace=True, actor=actor_name())
                    log_event(
                        conn,
                        "更新材料数据文件",
                        "material_data",
                        clean_original_filename(file.filename, fallback_suffix=".xlsx"),
                        f"型号 {stats['model_count']} 个，明细 {stats['detail_count']} 行；规格尺寸重算 {normalized} 行；导入数据库 {imported} 行",
                        actor=actor_name(),
                    )
                    conn.commit()
        except ImportLockError as exc:
            flash(str(exc), "error")
            return redirect(url_for("material_items"))
        except Exception as exc:
            flash(f"材料数据导入失败：{exc}", "error")
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
        bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH)
        with connect(DB_PATH) as conn:
            item = get_material_item(conn, item_id)
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
            with connect(DB_PATH) as conn:
                upsert_material_item(conn, data, actor=actor_name())
        except Exception as exc:
            flash(f"保存失败：{exc}", "error")
            return redirect(url_for("material_items"))
        flash("材料明细已保存。", "success")
        return redirect(url_for("material_items", q=data["model"]))

    @app.post("/materials/items/<int:item_id>/deactivate")
    @permission_required("manage_materials")
    def stop_material_item(item_id: int):
        with connect(DB_PATH) as conn:
            deactivate_material_item(conn, item_id, actor=actor_name())
        flash("材料明细已停用。", "success")
        return redirect(url_for("material_items"))
