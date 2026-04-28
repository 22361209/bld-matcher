from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from flask import flash, redirect, render_template, request, send_file, url_for

from app.config import DATA_DIR, DB_PATH, MATERIAL_DATA_PATH, MATERIAL_TEMPLATE_PATH, OUTPUT_DIR, UPLOAD_DIR
from app.database import (
    bootstrap_materials_from_excel,
    connect,
    deactivate_material_item,
    get_material_item,
    import_materials_from_excel,
    list_material_items,
    log_event,
    material_item_stats,
    rows_for_material_sheet,
    upsert_material_item,
)
from app.helpers import clean_original_filename, safe_upload_name
from app.material_sheet import create_plan_template, generate_material_sheet_from_materials, material_data_stats
from app.security import actor_name, login_required, permission_required


def register(app) -> None:
    @app.get("/materials")
    @login_required
    def materials():
        bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH)
        query = request.args.get("q", "")
        status = request.args.get("status", "active")
        if status not in {"active", "all", "inactive"}:
            status = "active"
        with connect(DB_PATH) as conn:
            items = list_material_items(
                conn,
                query=query,
                include_inactive=status == "all",
                only_inactive=status == "inactive",
                limit=3000,
            )
            stats = material_item_stats(conn)
        latest_outputs = (
            sorted(OUTPUT_DIR.glob("*料单*.xlsx"), key=lambda path: path.stat().st_mtime, reverse=True)[:8]
            if OUTPUT_DIR.exists()
            else []
        )
        return render_template(
            "materials.html",
            material_file_stats=material_data_stats(MATERIAL_DATA_PATH),
            material_stats=stats,
            material_items=items,
            material_path=MATERIAL_DATA_PATH if MATERIAL_DATA_PATH.exists() else None,
            query=query,
            status=status,
            latest_outputs=latest_outputs,
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

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = UPLOAD_DIR / f"material-plan-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_upload_name(file.filename)}"
        file.save(upload_path)

        try:
            bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH)
            with connect(DB_PATH) as conn:
                material_rows = rows_for_material_sheet(conn)
            if not material_rows:
                raise ValueError("还没有可用的材料明细，请先上传或新增材料数据。")
            output_path, summary = generate_material_sheet_from_materials(material_rows, upload_path, OUTPUT_DIR)
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
            return redirect(url_for("materials"))
        if Path(file.filename).suffix.lower() != ".xlsx":
            flash("材料数据请使用 .xlsx 文件。", "error")
            return redirect(url_for("materials"))

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = UPLOAD_DIR / f"material-data-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_upload_name(file.filename)}"
        file.save(upload_path)
        stats = material_data_stats(upload_path)
        if stats.get("invalid"):
            flash(f"材料数据读取失败：{stats.get('error') or '文件里必须包含“材料数据”工作表。'}", "error")
            return redirect(url_for("materials"))

        if MATERIAL_DATA_PATH.exists():
            backup = DATA_DIR / f"stamping_materials-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
            shutil.copy2(MATERIAL_DATA_PATH, backup)
        shutil.copy2(upload_path, MATERIAL_DATA_PATH)
        try:
            with connect(DB_PATH) as conn:
                imported = import_materials_from_excel(conn, upload_path, replace=True, actor=actor_name())
                log_event(
                    conn,
                    "更新材料数据文件",
                    "material_data",
                    clean_original_filename(file.filename, fallback_suffix=".xlsx"),
                    f"型号 {stats['model_count']} 个，明细 {stats['detail_count']} 行；导入数据库 {imported} 行",
                    actor=actor_name(),
                )
                conn.commit()
        except Exception as exc:
            flash(f"材料数据导入失败：{exc}", "error")
            return redirect(url_for("materials"))
        flash("材料数据已更新并导入数据库。", "success")
        return redirect(url_for("materials"))

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
            return redirect(url_for("materials"))
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
            return redirect(url_for("materials"))
        flash("材料明细已保存。", "success")
        return redirect(url_for("materials", q=data["model"]))

    @app.post("/materials/items/<int:item_id>/deactivate")
    @permission_required("manage_materials")
    def stop_material_item(item_id: int):
        with connect(DB_PATH) as conn:
            deactivate_material_item(conn, item_id, actor=actor_name())
        flash("材料明细已停用。", "success")
        return redirect(url_for("materials"))
