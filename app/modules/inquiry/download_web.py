from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
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
    customer_code_column_from_request,
    match_column_payload,
    match_columns_display,
    optional_match_columns,
    price_log_text,
    price_options_from_request,
    selected_match_columns,
    validated_user_upload_path,
)
from app.modules.quotes.domain import QuoteValidationError
from app.modules.quotes.factory import get_quote_service
from app.security import actor_name, permission_required


logger = logging.getLogger(__name__)


def _write_quote_price_fields(price_cny: float, price_options: dict) -> dict:
    mode = price_options.get("price_mode")
    if mode == "tax":
        return {"tax_price": round(price_cny, 2), "currency": "CNY"}
    if mode == "net":
        net_price = (Decimal(str(price_cny)) / Decimal("1.1")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
        return {"net_price": int(net_price), "currency": "CNY"}
    if mode == "usd":
        exchange_rate = float(price_options.get("exchange_rate") or 0)
        return {"tax_price": round(price_cny / 1.1 / exchange_rate, 2), "currency": "USD"}
    return {}


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

    @app.post("/match/write-quotes")
    @permission_required("manage_customer_prices")
    def write_match_quotes():
        service = get_inquiry_service()
        if not service.catalog_available():
            flash("请先上传产品目录。", "error")
            return redirect(url_for("index"))

        upload_path = validated_user_upload_path()
        if not upload_path:
            flash("询价源文件不存在，请重新上传。", "error")
            return redirect(url_for("index"))

        customer_name = request.form.get("customer_name", "").strip()
        if not customer_name:
            flash("写入报价前请填写客户名称。", "error")
            return redirect(url_for("index"))

        price_options, price_error = price_options_from_request()
        if price_error:
            flash(price_error, "error")
            return redirect(url_for("index"))
        if price_options.get("price_mode") == "none":
            flash("未选择单价方式，无法写入报价；请先在下载弹窗中选择含税或不含税单价。", "error")
            return redirect(url_for("index"))

        match_columns = optional_match_columns() or []
        customer_code_column = customer_code_column_from_request()
        original_filename = request.form.get("original_filename") or upload_path.name
        remark = request.form.get("remark", "").strip()
        try:
            summary = service.analyze_workbook(
                upload_path,
                user_output_dir() / "__write-quotes-analysis.xlsx",
                match_column=match_column_payload(match_columns),
                write_output=False,
                customer_code_column=customer_code_column,
            )
        except ValueError as exc:
            flash(f"生成失败：{exc}", "error")
            return redirect(url_for("index"))
        except Exception:
            logger.exception("Quote write-back analysis failed")
            flash("生成失败，请稍后重试。", "error")
            return redirect(url_for("index"))

        quote_service = get_quote_service()
        actor = actor_name()
        seen_keys: set[tuple[str, str]] = set()
        written = 0
        skipped = 0
        for row in summary["rows"]:
            bld_no = str(row.get("bld_no") or "").strip()
            price_cny = row.get("price_cny")
            if not bld_no or " / " in bld_no or price_cny is None:
                skipped += 1
                continue
            customer_product_code = str(row.get("customer_product_code") or "").strip()
            key = (bld_no, customer_product_code)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            data = {
                "customer_name": customer_name,
                "bld_no": bld_no,
                "customer_product_code": customer_product_code,
                "quoted_by": actor,
                "source_type": "excel",
                "source_text": original_filename,
                "remark": remark,
            }
            data.update(_write_quote_price_fields(float(price_cny), price_options))
            try:
                quote_service.create(data, actor=actor)
            except QuoteValidationError:
                skipped += 1
                continue
            written += 1

        if written:
            flash(f"已写入 {written} 条报价记录（跳过 {skipped} 条未命中或无单价行）。", "success")
        else:
            flash("没有可写入的报价行：需要命中有 BLD 号且带单价的条目。", "error")
        return redirect(url_for("quote_web.quotes", customer_name=customer_name) + "#quote-results")

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
