from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from flask import flash, redirect, render_template, request, send_file, url_for

from app.excel_io import PRICE_EXPORT_MODES, sanitize_inquiry_workbook_if_needed
from app.helpers import (
    clean_original_filename,
    column_display,
    result_output_path,
    unique_prefixed_path,
    user_file_label,
    user_output_dir,
    user_upload_dir,
    user_upload_path,
)
from app.matcher import normalize_code
from app.modules.inquiry.domain import parse_price_options
from app.modules.inquiry.factory import get_inquiry_service
from app.security import actor_name, permission_required


PASTED_INQUIRY_FILENAME = "粘贴号码询价.xlsx"
PASTED_INQUIRY_MAX_CHARS = 5000
logger = logging.getLogger(__name__)


def _validated_user_upload_path() -> Path | None:
    upload_path = Path(request.form.get("upload_path", "")).resolve()
    user_upload_root = user_upload_dir(create=False).resolve()
    if user_upload_root not in upload_path.parents or not upload_path.exists():
        return None
    return upload_path


def _match_columns_from_request(required: bool) -> list[int] | None:
    raw_values = request.form.getlist("match_columns")
    if not raw_values:
        raw_values = [request.form.get("match_column", "")]

    columns: list[int] = []
    seen = set()
    for value in raw_values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            column = int(text)
        except ValueError:
            return None
        if column < 0 or column in seen:
            continue
        seen.add(column)
        columns.append(column)

    if required and not columns:
        return None
    return columns


def _selected_match_columns() -> list[int] | None:
    return _match_columns_from_request(required=True)


def _optional_match_columns() -> list[int]:
    return _match_columns_from_request(required=False) or []


def _match_column_payload(match_columns: list[int]) -> object:
    if not match_columns:
        return None
    if len(match_columns) == 1:
        return match_columns[0]
    return match_columns


def _match_columns_display(match_columns: list[int]) -> str:
    return "、".join(column_display(column) for column in match_columns)


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


def _price_log_text(price_options: dict) -> str:
    mode = price_options.get("price_mode", "none")
    if mode == "usd":
        return f"；导出美金价，汇率 {price_options.get('exchange_rate')}"
    if mode == "tax":
        return "；导出含税单价"
    if mode == "net":
        return "；导出不含税单价"
    return ""


def _clean_inquiry_workbook_for_matching(upload_path: Path, original_filename: str) -> tuple[Path, str]:
    cleanup = sanitize_inquiry_workbook_if_needed(
        upload_path,
        user_upload_path(original_filename, prefix="inquiry-cleaned"),
    )
    if not cleanup.cleaned:
        return upload_path, ""

    get_inquiry_service().record_cleanup(
        clean_original_filename(original_filename, fallback_suffix=upload_path.suffix),
        cleanup.message,
        actor=actor_name(),
    )
    return cleanup.path, cleanup.message


def _render_pasted_inquiry_result(query: str):
    service = get_inquiry_service()
    codes = service.pasted_codes(query)
    if not service.should_render_pasted(query, codes):
        return redirect(url_for("index", quick_oe=query))

    upload_path = user_upload_path("pasted-oe.xlsx", prefix="inquiry-text")
    output_path = result_output_path(PASTED_INQUIRY_FILENAME, fallback_suffix=".xlsx")
    try:
        summary = service.analyze_pasted(codes, upload_path=upload_path, actor=actor_name())
    except ValueError as exc:
        flash(f"生成失败：{exc}", "error")
        return redirect(url_for("index", quick_oe=query))
    except Exception:
        logger.exception("Pasted inquiry analysis failed")
        flash("生成失败，请稍后重试。", "error")
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
        service = get_inquiry_service()
        if not service.catalog_available():
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        file = request.files.get("inquiry")
        if not file or not file.filename:
            quick_oe = request.form.get("quick_oe", "").strip()
            if quick_oe:
                if len(quick_oe) > PASTED_INQUIRY_MAX_CHARS:
                    flash(f"粘贴号码最多支持 {PASTED_INQUIRY_MAX_CHARS} 个字符，请改用 Excel 文件导入。", "error")
                    return redirect(url_for("index"))
                return _render_pasted_inquiry_result(quick_oe)
            flash("请选择客户询价文件或输入 OE、品牌号码或 BLD 号。", "error")
            return redirect(url_for("index"))
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {".xls", ".xlsx"}:
            flash("客户源文件支持 .xls 和 .xlsx，并会保持格式新增列。", "error")
            return redirect(url_for("index"))

        upload_path = user_upload_path(file.filename, prefix="inquiry")
        file.save(upload_path)
        match_upload_path, cleanup_message = _clean_inquiry_workbook_for_matching(upload_path, file.filename)

        output_path = result_output_path(file.filename, fallback_suffix=suffix)
        output_name = output_path.name
        preview = service.preview_columns(match_upload_path)
        return render_template(
            "select_match_column.html",
            upload_path=match_upload_path,
            original_filename=clean_original_filename(file.filename, fallback_suffix=suffix),
            output_name=output_name,
            preview=preview,
            cleanup_message=cleanup_message,
        )

    @app.post("/match/column")
    @permission_required("generate_match")
    def match_inquiry_with_column():
        service = get_inquiry_service()
        if not service.catalog_available():
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        upload_path = _validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_columns = _selected_match_columns()
        if match_columns is None:
            flash("请选择有效的匹配列。", "error")
            return redirect(url_for("index"))

        original_filename = request.form.get("original_filename") or upload_path.name
        output_name = request.form.get("output_name")
        output_path = user_output_dir() / Path(output_name).name if output_name else result_output_path(original_filename, fallback_suffix=upload_path.suffix)
        try:
            summary = service.analyze_workbook(
                upload_path,
                output_path,
                match_column=_match_column_payload(match_columns),
                write_output=False,
            )
        except ValueError as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("index"))
        except Exception:
            logger.exception("Inquiry preview generation failed")
            flash("生成失败，请稍后重试。", "error")
            return redirect(url_for("index"))

        return render_template(
            "result.html",
            summary=summary,
            output_path=output_path,
            output_pending=True,
            upload_path=upload_path,
            original_filename=original_filename,
            output_name=output_path.name,
            match_columns=match_columns,
            cleanup_message=request.form.get("cleanup_message", ""),
        )

    @app.post("/match/column/back")
    @permission_required("generate_match")
    def back_to_match_column():
        upload_path = _validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        selected_columns = _optional_match_columns() or [0]
        preview = get_inquiry_service().preview_columns(upload_path)
        return render_template(
            "select_match_column.html",
            upload_path=upload_path,
            original_filename=request.form.get("original_filename") or upload_path.name,
            output_name=request.form.get("output_name") or result_output_path(upload_path.name, fallback_suffix=upload_path.suffix).name,
            preview=preview,
            selected_columns=selected_columns,
            cleanup_message=request.form.get("cleanup_message", ""),
        )

    def _send_match_result_download(require_match_column: bool = False):
        service = get_inquiry_service()
        if not service.catalog_available():
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        upload_path = _validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_columns = _selected_match_columns() if require_match_column else _optional_match_columns()
        if require_match_column and match_columns is None:
            flash("请选择有效的匹配列。", "error")
            return redirect(url_for("index"))
        match_columns = match_columns or []

        price_options, price_error = _price_options_from_request()
        if price_error:
            flash(price_error, "error")
            return redirect(url_for("index"))

        original_filename = request.form.get("original_filename") or upload_path.name
        output_name = Path(request.form.get("output_name") or "").name
        output_path = user_output_dir() / output_name if output_name else result_output_path(original_filename, fallback_suffix=upload_path.suffix)
        try:
            summary = service.analyze_workbook(
                upload_path,
                output_path,
                match_column=_match_column_payload(match_columns),
                write_output=True,
                options=parse_price_options(price_options, default="none"),
            )
            detail_prefix = (
                f"手动选择 {_match_columns_display(match_columns)} 列；" if match_columns else ""
            )
            service.record_export(
                original_filename,
                summary,
                detail_prefix,
                detail_suffix=_price_log_text(price_options),
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("index"))
        except Exception:
            logger.exception("Inquiry export failed")
            flash("生成失败，请稍后重试。", "error")
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
        service = get_inquiry_service()
        if not service.catalog_available():
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        upload_path = _validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_columns = _optional_match_columns()
        original_filename = request.form.get("original_filename") or upload_path.name
        safe_original = clean_original_filename(original_filename, fallback_suffix=upload_path.suffix)
        source_stem = Path(safe_original).stem or "inquiry"
        zip_path = unique_prefixed_path(
            user_output_dir(),
            f"drawings-{datetime.now().strftime('%y%m%d')}-{user_file_label()}-{source_stem}.zip",
        )
        try:
            summary = service.analyze_workbook(
                upload_path,
                user_output_dir() / "__drawing-match-preview.xlsx",
                match_column=_match_column_payload(match_columns),
                write_output=False,
            )
            detail_prefix = (
                f"手动选择 {_match_columns_display(match_columns)} 列；" if match_columns else ""
            )
            service.package_drawings(
                summary["rows"],
                zip_path,
                detail_prefix=detail_prefix,
                matched=summary["matched"],
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"图纸打包失败：{exc}", "error")
            return redirect(url_for("index"))
        except Exception:
            logger.exception("Inquiry drawing package failed")
            flash("图纸打包失败，请稍后重试。", "error")
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

        sync_target = request.form.get("sync_target", "oe")
        appended = get_inquiry_service().save_alias(
            source_code,
            bld_no,
            request.form.get("note", ""),
            sync_target,
            actor=actor_name(),
        )
        target_label = "OE 号" if request.form.get("sync_target", "oe") == "oe" else "品牌号码"
        flash("人工映射已保存。" + (f" 已同步加入产品目录{target_label}。" if appended else ""), "success")
        return redirect(url_for("index"))

    @app.post("/manual-map/delete")
    @permission_required("manage_aliases")
    def delete_manual_map():
        source_code = request.form.get("source_code", "")
        get_inquiry_service().delete_alias(source_code, actor=actor_name())
        flash("人工映射已删除。", "success")
        return redirect(url_for("index"))
