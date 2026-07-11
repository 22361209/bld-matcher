from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from flask import flash, redirect, request, send_file, url_for

from app.helpers import (
    clean_original_filename,
    result_output_path,
    unique_prefixed_path,
    user_file_label,
    user_output_dir,
)
from app.modules.inquiry.domain import parse_price_options
from app.modules.inquiry.factory import get_inquiry_service
from app.modules.inquiry.web_helpers import (
    match_column_payload,
    match_columns_display,
    optional_match_columns,
    price_log_text,
    price_options_from_request,
    selected_match_columns,
    validated_user_upload_path,
)
from app.security import actor_name, permission_required


logger = logging.getLogger(__name__)


def register(app) -> None:
    def _send_match_result_download(require_match_column: bool = False):
        service = get_inquiry_service()
        if not service.catalog_available():
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        upload_path = validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_columns = selected_match_columns() if require_match_column else optional_match_columns()
        if require_match_column and match_columns is None:
            flash("请选择有效的匹配列。", "error")
            return redirect(url_for("index"))
        match_columns = match_columns or []

        price_options, price_error = price_options_from_request()
        if price_error:
            flash(price_error, "error")
            return redirect(url_for("index"))

        original_filename = request.form.get("original_filename") or upload_path.name
        output_name = Path(request.form.get("output_name") or "").name
        output_path = (
            user_output_dir() / output_name
            if output_name
            else result_output_path(original_filename, fallback_suffix=upload_path.suffix)
        )
        try:
            summary = service.analyze_workbook(
                upload_path,
                output_path,
                match_column=match_column_payload(match_columns),
                write_output=True,
                options=parse_price_options(price_options, default="none"),
            )
            detail_prefix = f"手动选择 {match_columns_display(match_columns)} 列；" if match_columns else ""
            service.record_export(
                original_filename,
                summary,
                detail_prefix,
                detail_suffix=price_log_text(price_options),
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

        upload_path = validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        match_columns = optional_match_columns()
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
                match_column=match_column_payload(match_columns),
                write_output=False,
            )
            detail_prefix = f"手动选择 {match_columns_display(match_columns)} 列；" if match_columns else ""
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
