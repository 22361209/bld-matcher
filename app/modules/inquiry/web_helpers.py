from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from flask import request

from app.excel_io import PRICE_EXPORT_MODES
from app.helpers import column_display, result_output_path, user_upload_dir
from app.modules.inquiry.adjustments import InquiryAdjustment, parse_adjustments


def validated_user_upload_path() -> Path | None:
    upload_path = Path(request.form.get("upload_path", "")).resolve()
    user_upload_root = user_upload_dir(create=False).resolve()
    if user_upload_root not in upload_path.parents or not upload_path.exists():
        return None
    return upload_path


def normalized_workbook_output_path(output_path: Path, source_suffix: str) -> Path:
    normalized_suffix = str(source_suffix or output_path.suffix).lower()
    if normalized_suffix in {".xls", ".xlsx"}:
        return output_path.with_suffix(normalized_suffix)
    return output_path


def quote_output_paths(original_filename: str, fallback_suffix: str) -> tuple[Path, Path]:
    """Return request-unique final and temporary quote attachment paths."""
    base_path = normalized_workbook_output_path(
        result_output_path(original_filename, fallback_suffix=fallback_suffix),
        fallback_suffix,
    )
    request_id = uuid4().hex
    output_path = base_path.with_name(f"{base_path.stem}-{request_id}{base_path.suffix}")
    temporary_path = output_path.with_name(f".{output_path.stem}.tmp{output_path.suffix}")
    return output_path, temporary_path


def match_columns_from_request(*, required: bool) -> list[int] | None:
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


def selected_match_columns() -> list[int] | None:
    return match_columns_from_request(required=True)


def optional_match_columns() -> list[int]:
    return match_columns_from_request(required=False) or []


def customer_code_column_from_request() -> int | None:
    raw = request.form.get("customer_code_column", "").strip()
    if not raw:
        return None
    try:
        column = int(raw)
    except ValueError:
        return None
    return column if column >= 0 else None


def match_column_payload(match_columns: list[int]) -> int | list[int] | None:
    if not match_columns:
        return None
    if len(match_columns) == 1:
        return match_columns[0]
    return match_columns


def match_columns_display(match_columns: list[int]) -> str:
    return "、".join(column_display(column) for column in match_columns)


def price_options_from_request() -> tuple[dict[str, object], str | None]:
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


def price_log_text(price_options: dict[str, object]) -> str:
    mode = price_options.get("price_mode", "none")
    if mode == "usd":
        return f"；导出美金价，汇率 {price_options.get('exchange_rate')}"
    if mode == "tax":
        return "；导出含税单价"
    if mode == "net":
        return "；导出不含税单价"
    return ""


def adjustments_from_request() -> dict[str, InquiryAdjustment]:
    raw = request.form.get("inquiry_adjustments", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("本次报价调整数据无效，请重新查询后再试。") from exc
    return parse_adjustments(payload)
