from __future__ import annotations

import logging
from pathlib import Path

from flask import flash, redirect, render_template, request, url_for

from app.excel_io import sanitize_inquiry_workbook_if_needed
from app.helpers import clean_original_filename, result_output_path, user_output_dir, user_upload_path
from app.modules.inquiry.factory import get_inquiry_service
from app.modules.inquiry.web_helpers import (
    match_column_payload,
    optional_match_columns,
    selected_match_columns,
    validated_user_upload_path,
)
from app.security import actor_name, permission_required


PASTED_INQUIRY_FILENAME = "粘贴号码询价.xlsx"
PASTED_INQUIRY_MAX_CHARS = 5000
logger = logging.getLogger(__name__)


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

        upload_path = validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_columns = selected_match_columns()
        if match_columns is None:
            flash("请选择有效的匹配列。", "error")
            return redirect(url_for("index"))

        original_filename = request.form.get("original_filename") or upload_path.name
        output_name = request.form.get("output_name")
        output_path = (
            user_output_dir() / Path(output_name).name
            if output_name
            else result_output_path(original_filename, fallback_suffix=upload_path.suffix)
        )
        try:
            summary = service.analyze_workbook(
                upload_path,
                output_path,
                match_column=match_column_payload(match_columns),
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
        upload_path = validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        selected_columns = optional_match_columns() or [0]
        preview = get_inquiry_service().preview_columns(upload_path)
        return render_template(
            "select_match_column.html",
            upload_path=upload_path,
            original_filename=request.form.get("original_filename") or upload_path.name,
            output_name=request.form.get("output_name")
            or result_output_path(upload_path.name, fallback_suffix=upload_path.suffix).name,
            preview=preview,
            selected_columns=selected_columns,
            cleanup_message=request.form.get("cleanup_message", ""),
        )
