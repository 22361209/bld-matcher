from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from flask import flash, redirect, render_template, request, send_file, url_for
from openpyxl import Workbook

from app.config import DB_PATH
from app.database import append_product_code, connect, delete_alias, log_event, save_alias
from app.drawings import build_drawings_zip
from app.excel_io import PRICE_EXPORT_MODES, generate_excel_with_bld, preview_inquiry_columns
from app.helpers import (
    clean_original_filename,
    column_display,
    load_catalog,
    result_output_path,
    unique_prefixed_path,
    user_file_label,
    user_output_dir,
    user_upload_dir,
    user_upload_path,
)
from app.matcher import normalize_code
from app.security import actor_name, permission_required


PASTED_INQUIRY_FILENAME = "粘贴号码询价.xlsx"


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


def _optional_match_column() -> int | None:
    value = request.form.get("match_column", "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _price_options_from_request() -> tuple[dict, str | None]:
    mode = request.form.get("price_mode", "none").strip() or "none"
    if mode not in PRICE_EXPORT_MODES:
        mode = "none"

    raw_rate = request.form.get("exchange_rate", "").strip()
    exchange_rate = None
    if mode == "usd":
        try:
            exchange_rate = float(raw_rate)
        except ValueError:
            return {"price_mode": mode, "exchange_rate": raw_rate}, "选择美金价时，请填写有效汇率。"
        if exchange_rate <= 0:
            return {"price_mode": mode, "exchange_rate": raw_rate}, "美金价汇率必须大于 0。"

    return {"price_mode": mode, "exchange_rate": exchange_rate, "exchange_rate_text": raw_rate}, None


def _price_generation_options(price_options: dict) -> dict:
    return {
        "price_mode": price_options.get("price_mode", "none"),
        "exchange_rate": price_options.get("exchange_rate"),
    }


def _price_log_text(price_options: dict) -> str:
    mode = price_options.get("price_mode", "none")
    if mode == "usd":
        return f"；导出美金价，汇率 {price_options.get('exchange_rate')}"
    if mode == "tax":
        return "；导出含税单价"
    return ""


def _pasted_inquiry_codes(value: str) -> list[str]:
    text = value.strip()
    if not text:
        return []

    split_codes = [part.strip() for part in re.split(r"[\n\r\t,，;；、/]+", text) if normalize_code(part)]
    if len(split_codes) > 1:
        return split_codes

    whitespace_codes = [part.strip() for part in re.split(r"\s+", text) if normalize_code(part)]
    if len(whitespace_codes) > 1:
        return whitespace_codes

    return split_codes or ([text] if normalize_code(text) else [])


def _save_pasted_inquiry_workbook(codes: list[str]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "粘贴号码"
    sheet.append(["OE号"])
    for code in codes:
        sheet.append([code])
    sheet.column_dimensions["A"].width = 24

    upload_path = user_upload_path("pasted-oe.xlsx", prefix="inquiry-text")
    workbook.save(upload_path)
    return upload_path


def _renumber_pasted_summary_rows(summary: dict) -> None:
    for index, row in enumerate(summary.get("rows", []), start=1):
        row["row"] = index


def _render_pasted_inquiry_result(catalog, query: str):
    codes = _pasted_inquiry_codes(query)
    if len(codes) <= 1:
        return redirect(url_for("index", quick_oe=query))

    upload_path = _save_pasted_inquiry_workbook(codes)
    output_path = result_output_path(PASTED_INQUIRY_FILENAME, fallback_suffix=".xlsx")
    try:
        summary = generate_excel_with_bld(upload_path, output_path, catalog, write_output=False)
        _renumber_pasted_summary_rows(summary)
        with connect(DB_PATH) as conn:
            log_event(
                conn,
                "预览粘贴号码匹配结果",
                "inquiry",
                PASTED_INQUIRY_FILENAME,
                f"粘贴 {len(codes)} 个号码；共 {summary['total']} 行，命中 {summary['matched']} 行，未找到 {summary['unmatched']} 行",
                actor=actor_name(),
            )
            conn.commit()
    except Exception as exc:
        flash(f"生成失败：{exc}", "error")
        return redirect(url_for("index", quick_oe=query))

    return render_template(
        "result.html",
        summary=summary,
        output_path=output_path,
        output_pending=True,
        upload_path=upload_path,
        original_filename=PASTED_INQUIRY_FILENAME,
        output_name=output_path.name,
        match_column="",
    )


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
                return _render_pasted_inquiry_result(catalog, quick_oe)
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
            summary = generate_excel_with_bld(upload_path, output_path, catalog, write_output=False)
            with connect(DB_PATH) as conn:
                log_event(
                    conn,
                    "预览匹配结果",
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

        return render_template(
            "result.html",
            summary=summary,
            output_path=output_path,
            output_pending=True,
            upload_path=upload_path,
            original_filename=clean_original_filename(file.filename, fallback_suffix=suffix),
            output_name=output_name,
            match_column="",
        )

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
            summary = generate_excel_with_bld(
                upload_path,
                output_path,
                catalog,
                match_column=match_column,
                write_output=False,
            )
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

    def _send_match_result_download(require_match_column: bool = False):
        catalog = load_catalog()
        if not catalog:
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        upload_path = _validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_column = _selected_match_column() if require_match_column else _optional_match_column()
        if require_match_column and match_column is None:
            flash("请选择有效的匹配列。", "error")
            return redirect(url_for("index"))

        price_options, price_error = _price_options_from_request()
        if price_error:
            flash(price_error, "error")
            return redirect(url_for("index"))

        original_filename = request.form.get("original_filename") or upload_path.name
        output_name = Path(request.form.get("output_name") or "").name
        output_path = user_output_dir() / output_name if output_name else result_output_path(original_filename, fallback_suffix=upload_path.suffix)
        try:
            summary = generate_excel_with_bld(
                upload_path,
                output_path,
                catalog,
                match_column=match_column,
                **_price_generation_options(price_options),
            )
            detail = f"共 {summary['total']} 行，命中 {summary['matched']} 行，未找到 {summary['unmatched']} 行{_price_log_text(price_options)}"
            if match_column is not None:
                detail = f"手动选择 {column_display(match_column)} 列；" + detail
            with connect(DB_PATH) as conn:
                log_event(conn, "生成匹配结果", "inquiry", original_filename, detail, actor=actor_name())
                conn.commit()
        except Exception as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("index"))

        return send_file(output_path, as_attachment=True)

    @app.post("/match/download")
    @permission_required("generate_match")
    def download_match_result():
        return _send_match_result_download()

    @app.post("/match/column/download")
    @permission_required("generate_match")
    def download_match_column_result():
        return _send_match_result_download(require_match_column=True)

    @app.post("/match/drawings/download")
    @permission_required("generate_match")
    def download_match_drawings():
        catalog = load_catalog()
        if not catalog:
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        upload_path = _validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_column = _optional_match_column()
        original_filename = request.form.get("original_filename") or upload_path.name
        safe_original = clean_original_filename(original_filename, fallback_suffix=upload_path.suffix)
        source_stem = Path(safe_original).stem or "inquiry"
        zip_path = unique_prefixed_path(
            user_output_dir(),
            f"drawings-{datetime.now().strftime('%y%m%d')}-{user_file_label()}-{source_stem}.zip",
        )
        try:
            summary = generate_excel_with_bld(
                upload_path,
                user_output_dir() / "__drawing-match-preview.xlsx",
                catalog,
                match_column=match_column,
                write_output=False,
            )
            with connect(DB_PATH) as conn:
                package = build_drawings_zip(conn, summary["rows"], zip_path)
                detail = f"共 {summary['matched']} 行命中，打包 PDF {package['added']} 个，缺少 {package['missing']} 个"
                if match_column is not None:
                    detail = f"手动选择 {column_display(match_column)} 列；" + detail
                log_event(conn, "生成图纸压缩包", "drawing_zip", zip_path.name, detail, actor=actor_name())
                conn.commit()
        except Exception as exc:
            flash(f"图纸打包失败：{exc}", "error")
            return redirect(url_for("index"))

        return send_file(zip_path, as_attachment=True)

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
