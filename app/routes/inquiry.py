from __future__ import annotations

from pathlib import Path

from flask import flash, redirect, render_template, request, send_file, url_for

from app.config import DB_PATH
from app.database import append_product_code, connect, delete_alias, log_event, save_alias
from app.excel_io import generate_excel_with_bld, preview_inquiry_columns
from app.helpers import clean_original_filename, column_display, load_catalog, result_output_path, user_output_dir, user_upload_dir, user_upload_path
from app.matcher import normalize_code
from app.security import actor_name, permission_required


def _validated_user_upload_path() -> Path | None:
    upload_path = Path(request.form.get("upload_path", "")).resolve()
    user_upload_root = user_upload_dir(create=False).resolve()
    if user_upload_root not in upload_path.parents or not upload_path.exists():
        return None
    return upload_path


def _selected_match_column() -> int | None:
    try:
        return int(request.form.get("match_column", "0"))
    except ValueError:
        return None


def register(app) -> None:
    @app.post("/match")
    @permission_required("generate_match")
    def match_inquiry():
        catalog = load_catalog()
        if not catalog:
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        file = request.files.get("inquiry")
        if not file or not file.filename:
            quick_oe = request.form.get("quick_oe", "").strip()
            if quick_oe:
                return redirect(url_for("index", quick_oe=quick_oe))
            flash("请选择客户询价文件或输入 OE 号码。", "error")
            return redirect(url_for("index"))
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {".xls", ".xlsx"}:
            flash("客户源文件支持 .xls 和 .xlsx，并会保持格式新增列。", "error")
            return redirect(url_for("index"))

        upload_path = user_upload_path(file.filename, prefix="inquiry")
        file.save(upload_path)

        output_path = result_output_path(file.filename, fallback_suffix=suffix)
        output_name = output_path.name
        try:
            summary = generate_excel_with_bld(upload_path, output_path, catalog)
            with connect(DB_PATH) as conn:
                log_event(
                    conn,
                    "生成匹配结果",
                    "inquiry",
                    clean_original_filename(file.filename, fallback_suffix=suffix),
                    f"共 {summary['total']} 行，命中 {summary['matched']} 行，未找到 {summary['unmatched']} 行",
                    actor=actor_name(),
                )
                conn.commit()
        except Exception as exc:
            if "询价表没有找到可识别表头" in str(exc):
                preview = preview_inquiry_columns(upload_path)
                return render_template(
                    "select_match_column.html",
                    upload_path=upload_path,
                    original_filename=clean_original_filename(file.filename, fallback_suffix=suffix),
                    output_name=output_name,
                    preview=preview,
                )
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("index"))

        return render_template("result.html", summary=summary, output_path=output_path)

    @app.post("/match/column")
    @permission_required("generate_match")
    def match_inquiry_with_column():
        catalog = load_catalog()
        if not catalog:
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        upload_path = _validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_column = _selected_match_column()
        if match_column is None:
            flash("请选择有效的匹配列。", "error")
            return redirect(url_for("index"))

        original_filename = request.form.get("original_filename") or upload_path.name
        output_name = request.form.get("output_name")
        output_path = user_output_dir() / Path(output_name).name if output_name else result_output_path(original_filename, fallback_suffix=upload_path.suffix)
        try:
            summary = generate_excel_with_bld(upload_path, output_path, catalog, match_column=match_column, write_output=False)
        except Exception as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("index"))

        return render_template(
            "result.html",
            summary=summary,
            output_path=output_path,
            output_pending=True,
            upload_path=upload_path,
            original_filename=original_filename,
            output_name=output_path.name,
            match_column=match_column,
        )

    @app.post("/match/column/back")
    @permission_required("generate_match")
    def back_to_match_column():
        upload_path = _validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        selected_column = _selected_match_column()
        if selected_column is None:
            selected_column = 0
        preview = preview_inquiry_columns(upload_path)
        return render_template(
            "select_match_column.html",
            upload_path=upload_path,
            original_filename=request.form.get("original_filename") or upload_path.name,
            output_name=request.form.get("output_name") or result_output_path(upload_path.name, fallback_suffix=upload_path.suffix).name,
            preview=preview,
            selected_column=selected_column,
        )

    @app.post("/match/column/download")
    @permission_required("generate_match")
    def download_match_column_result():
        catalog = load_catalog()
        if not catalog:
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        upload_path = _validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_column = _selected_match_column()
        if match_column is None:
            flash("请选择有效的匹配列。", "error")
            return redirect(url_for("index"))

        original_filename = request.form.get("original_filename") or upload_path.name
        output_name = Path(request.form.get("output_name") or "").name
        output_path = user_output_dir() / output_name if output_name else result_output_path(original_filename, fallback_suffix=upload_path.suffix)
        try:
            summary = generate_excel_with_bld(upload_path, output_path, catalog, match_column=match_column)
            with connect(DB_PATH) as conn:
                log_event(
                    conn,
                    "生成匹配结果",
                    "inquiry",
                    original_filename,
                    f"手动选择 {column_display(match_column)} 列；共 {summary['total']} 行，命中 {summary['matched']} 行，未找到 {summary['unmatched']} 行",
                    actor=actor_name(),
                )
                conn.commit()
        except Exception as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("index"))

        return send_file(output_path, as_attachment=True)

    @app.post("/manual-map")
    @permission_required("manage_aliases")
    def add_manual_map():
        source_code = request.form.get("source_code", "")
        bld_no = request.form.get("bld_no", "")
        if not normalize_code(source_code) or not normalize_code(bld_no):
            flash("请输入客户号码和 BLD NO.。", "error")
            return redirect(url_for("index"))

        with connect(DB_PATH) as conn:
            save_alias(conn, source_code, bld_no, request.form.get("note", ""), actor=actor_name())
            appended = False
            sync_target = request.form.get("sync_target", "oe")
            if sync_target in {"oe", "brand_code"}:
                appended = append_product_code(conn, bld_no, source_code, target=sync_target, actor=actor_name())
        target_label = "OE 号" if request.form.get("sync_target", "oe") == "oe" else "品牌号码"
        flash("人工映射已保存。" + (f" 已同步加入产品目录{target_label}。" if appended else ""), "success")
        return redirect(url_for("index"))

    @app.post("/manual-map/delete")
    @permission_required("manage_aliases")
    def delete_manual_map():
        source_code = request.form.get("source_code", "")
        with connect(DB_PATH) as conn:
            delete_alias(conn, source_code, actor=actor_name())
        flash("人工映射已删除。", "success")
        return redirect(url_for("index"))
