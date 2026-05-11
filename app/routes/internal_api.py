from __future__ import annotations

import hmac
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from pathlib import Path

from flask import jsonify, request
from openpyxl import Workbook

from app.config import BASE_DIR, DB_PATH, INTERNAL_API_TOKEN, OUTPUT_DIR, UPLOAD_DIR
from app.database import connect, log_event, verify_internal_api_token
from app.excel_io import PRICE_EXPORT_MODES, generate_excel_with_bld, preview_inquiry_columns
from app.helpers import clean_original_filename, load_catalog, safe_upload_name, unique_prefixed_path
from app.matcher import normalize_code


INTERNAL_OUTPUT_DIR = OUTPUT_DIR / "openclaw"
INTERNAL_UPLOAD_DIR = UPLOAD_DIR / "openclaw"
ALLOWED_WORKBOOK_SUFFIXES = {".xls", ".xlsx"}
ALLOWED_FILE_PATH_ROOTS = tuple(path.resolve() for path in (BASE_DIR, UPLOAD_DIR, OUTPUT_DIR))
PRICE_LABELS = {
    "none": "",
    "tax": "含税单价",
    "net": "不含税单价",
    "usd": "美金价",
}


def _json_error(message: str, status: int = 400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


def _payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data

    form_payload: dict[str, object] = {}
    for key in request.form:
        values = request.form.getlist(key)
        form_payload[key] = values if len(values) > 1 else values[0]
    return form_payload


def _payload_value(payload: dict, *names: str, default=None):
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return value
    return default


def _client_addr() -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    return forwarded or request.remote_addr or ""


def _request_token() -> str:
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(None, 1)[1].strip()
    return request.headers.get("X-Internal-API-Token", "").strip()


def _authorized() -> bool:
    token = _request_token()
    if not token:
        return False
    if INTERNAL_API_TOKEN and hmac.compare_digest(token, INTERNAL_API_TOKEN):
        return True
    with connect(DB_PATH) as conn:
        return verify_internal_api_token(conn, token)


def internal_api_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _authorized():
            return _json_error("内部 API 未授权，请先在后台生成 API Key，并用 Authorization: Bearer <key> 调用。", 401)
        return fn(*args, **kwargs)

    return wrapper


def _parse_bool(value: object, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _parse_match_column(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        if value < 0:
            raise ValueError("match_column 不能小于 0。")
        return value

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if re.fullmatch(r"[A-Za-z]+", text):
        index = 0
        for char in text.upper():
            index = index * 26 + (ord(char) - ord("A") + 1)
        return index - 1
    raise ValueError("match_column 需要传 0 起始列号，或 Excel 列字母，例如 A。")


def _parse_price_options(payload: dict) -> tuple[dict | None, str | None]:
    mode = str(_payload_value(payload, "price_mode", default="tax") or "tax").strip().lower()
    if mode not in PRICE_EXPORT_MODES:
        return None, "price_mode 仅支持 none、tax、net、usd。"

    exchange_rate = None
    raw_rate = str(_payload_value(payload, "exchange_rate", default="") or "").strip()
    if mode == "usd":
        try:
            exchange_rate = float(raw_rate)
        except ValueError:
            return None, "选择美金价时，请传有效 exchange_rate。"
        if exchange_rate <= 0:
            return None, "exchange_rate 必须大于 0。"

    return {"price_mode": mode, "exchange_rate": exchange_rate, "exchange_rate_text": raw_rate}, None


def _decimal_price(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _export_price(value: object, price_options: dict) -> int | float | None:
    price = _decimal_price(value)
    if price is None:
        return None

    mode = price_options["price_mode"]
    if mode == "none":
        return None
    if mode == "tax":
        return float(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if mode == "net":
        return int((price / Decimal("1.1")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if mode == "usd":
        rate = Decimal(str(price_options["exchange_rate"]))
        return float((price / Decimal("1.1") / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return None


def _split_text_codes(text: str) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []

    parts = [part.strip() for part in re.split(r"[\n\r\t,，;；、/]+", text) if normalize_code(part)]
    if len(parts) > 1:
        return parts

    whitespace_parts = [part.strip() for part in re.split(r"\s+", text) if normalize_code(part)]
    if len(whitespace_parts) > 1:
        return whitespace_parts

    return parts or ([text] if normalize_code(text) else [])


def _extract_numbers(payload: dict) -> tuple[list[str], list[str]]:
    raw_numbers = _payload_value(payload, "numbers", "codes", default=[])
    raw_text = _payload_value(payload, "text", "query", default="")
    values: list[object] = []

    if isinstance(raw_numbers, str):
        values.extend(_split_text_codes(raw_numbers))
    elif isinstance(raw_numbers, (list, tuple)):
        values.extend(raw_numbers)
    elif raw_numbers:
        values.append(raw_numbers)

    if raw_text:
        values.extend(_split_text_codes(str(raw_text)))

    numbers: list[str] = []
    invalid_items: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if not normalize_code(text):
            invalid_items.append(text)
            continue
        numbers.append(text)
    return numbers, invalid_items


def _internal_upload_path(filename: str, *, prefix: str) -> Path:
    INTERNAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = safe_upload_name(filename)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return unique_prefixed_path(INTERNAL_UPLOAD_DIR, f"{prefix}-{timestamp}-{safe_name}")


def _save_numbers_workbook(numbers: list[str]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OpenClaw号码"
    sheet.append(["OE号"])
    for number in numbers:
        sheet.append([number])
    sheet.column_dimensions["A"].width = 28

    path = _internal_upload_path("numbers.xlsx", prefix="numbers")
    workbook.save(path)
    workbook.close()
    return path


def _source_from_request(payload: dict) -> tuple[Path | None, str | None, str | None]:
    file = request.files.get("file") or request.files.get("inquiry")
    if file and file.filename:
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_WORKBOOK_SUFFIXES:
            return None, None, "客户原始文件仅支持 .xls 或 .xlsx。"
        upload_path = _internal_upload_path(file.filename, prefix="source")
        file.save(upload_path)
        return upload_path, clean_original_filename(file.filename, fallback_suffix=suffix), None

    raw_path = _payload_value(payload, "file_path", "path", "source_path")
    if not raw_path:
        return None, None, "请传 file_path，或以 multipart/form-data 上传 file。"

    source_path = Path(str(raw_path)).expanduser()
    if not source_path.is_absolute():
        source_path = (BASE_DIR / source_path).resolve()
    else:
        source_path = source_path.resolve()
    if not source_path.exists() or not source_path.is_file():
        return None, None, f"文件不存在：{source_path}"
    if source_path.suffix.lower() not in ALLOWED_WORKBOOK_SUFFIXES:
        return None, None, "客户原始文件仅支持 .xls 或 .xlsx。"
    if not any(source_path == root or root in source_path.parents for root in ALLOWED_FILE_PATH_ROOTS):
        allowed = "、".join(str(root) for root in ALLOWED_FILE_PATH_ROOTS)
        return None, None, f"file_path 不在允许读取范围内。允许范围：{allowed}"
    return source_path, clean_original_filename(source_path.name, fallback_suffix=source_path.suffix), None


def _internal_output_path(filename: str) -> Path:
    INTERNAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return unique_prefixed_path(INTERNAL_OUTPUT_DIR, filename)


def _date_label() -> str:
    return datetime.now().strftime("%y%m%d")


def _output_source_stem(value: object, *, fallback: str) -> str:
    name = clean_original_filename(str(value or ""), fallback_suffix="")
    stem = Path(name).stem.strip()
    return stem or fallback


def _openclaw_output_name(source_name: object, *, suffix: str, fallback: str) -> str:
    stem = _output_source_stem(source_name, fallback=fallback)
    return f"re{_date_label()}_{stem}_openclaw{suffix}"


def _number_output_path(payload: dict) -> Path:
    source_name = _payload_value(payload, "source_name", "source_filename", "original_filename", "output_name")
    name = _openclaw_output_name(source_name, suffix=".xlsx", fallback="客户询价")
    return _internal_output_path(name)


def _source_output_path(payload: dict, original_filename: str, suffix: str) -> Path:
    source_name = _payload_value(payload, "source_name", "source_filename", default=original_filename)
    name = _openclaw_output_name(source_name, suffix=suffix, fallback="客户询价")
    return _internal_output_path(name)


def _format_rows(summary: dict, price_options: dict, *, rows_limit: int, unmatched_limit: int) -> tuple[list[dict], list[str], bool]:
    formatted_rows = []
    unmatched_list = []
    for row in summary.get("rows", []):
        matched = bool(row.get("bld_no"))
        if not matched and len(unmatched_list) < unmatched_limit:
            unmatched_list.append(str(row.get("oe") or row.get("name") or "").strip())

        export_price = _export_price(row.get("price_cny"), price_options)
        formatted_rows.append(
            {
                "row": row.get("row"),
                "original_number": row.get("oe"),
                "original_name": row.get("name"),
                "matched": matched,
                "bld_no": row.get("bld_no") or "",
                "match_reason": row.get("reason") or "",
                "match_note": row.get("match_note") or row.get("reason") or "",
                "score": row.get("score", 0),
                "price_cny": row.get("price_cny"),
                "export_price": export_price,
                "export_price_label": PRICE_LABELS.get(price_options["price_mode"], ""),
                "matched_oe_codes": row.get("matched_oe_codes") or [],
                "unmatched_oe_codes": row.get("unmatched_oe_codes") or [],
            }
        )

    rows_truncated = len(formatted_rows) > rows_limit
    return formatted_rows[:rows_limit], unmatched_list, rows_truncated


def _response_payload(
    *,
    mode: str,
    summary: dict,
    price_options: dict,
    output_path: Path | None,
    invalid_items: list[str] | None = None,
    source_path: Path | None = None,
    rows_limit: int = 200,
    unmatched_limit: int = 100,
) -> dict:
    rows, unmatched_list, rows_truncated = _format_rows(
        summary,
        price_options,
        rows_limit=rows_limit,
        unmatched_limit=unmatched_limit,
    )
    summary_payload = {
        "total_rows": summary.get("total", 0),
        "matched_count": summary.get("matched", 0),
        "unmatched_count": summary.get("unmatched", 0),
        "returned_rows": len(rows),
        "rows_truncated": rows_truncated,
        "invalid_items": invalid_items or [],
        "price_mode": price_options["price_mode"],
        "export_price_label": PRICE_LABELS.get(price_options["price_mode"], ""),
        "output_generated": output_path is not None,
    }
    return {
        "ok": True,
        "mode": mode,
        "summary": summary_payload,
        "matched_count": summary_payload["matched_count"],
        "unmatched_count": summary_payload["unmatched_count"],
        "rows": rows,
        "unmatched_list": unmatched_list,
        "invalid_items": invalid_items or [],
        "source_path": str(source_path.resolve()) if source_path else None,
        "output_path": str(output_path.resolve()) if output_path else None,
        "output_name": output_path.name if output_path else None,
    }


def _preview_for_error(path: Path) -> dict | None:
    try:
        return preview_inquiry_columns(path, max_rows=5, max_cols=8)
    except Exception:
        return None


def _load_catalog_or_error():
    catalog = load_catalog()
    if not catalog:
        return None, _json_error("请先导入产品目录。", 409)
    return catalog, None


def _run_numbers(payload: dict, *, export: bool):
    catalog, error_response = _load_catalog_or_error()
    if error_response:
        return error_response

    price_options, price_error = _parse_price_options(payload)
    if price_error:
        return _json_error(price_error)

    numbers, invalid_items = _extract_numbers(payload)
    if not numbers:
        return _json_error("请传 numbers 数组，或 text 文本号码。", invalid_items=invalid_items)

    rows_limit = _parse_int(_payload_value(payload, "rows_limit"), default=200, minimum=0, maximum=1000)
    unmatched_limit = _parse_int(_payload_value(payload, "unmatched_limit"), default=100, minimum=0, maximum=1000)
    if export and not _payload_value(payload, "source_name", "source_filename", "original_filename", "output_name"):
        return _json_error("号码数组或文字号码生成 Excel 时必须传 source_name，作为文件名中间的“源文件名称”。")

    source_path = _save_numbers_workbook(numbers)
    output_path = _number_output_path(payload) if export else None
    summary = generate_excel_with_bld(
        source_path,
        output_path or (INTERNAL_OUTPUT_DIR / "__analysis.xlsx"),
        catalog,
        write_output=export,
        price_mode=price_options["price_mode"],
        exchange_rate=price_options["exchange_rate"],
    )

    if export and output_path:
        with connect(DB_PATH) as conn:
            detail = f"OpenClaw 号码查询 {summary['total']} 行，命中 {summary['matched']} 行，未找到 {summary['unmatched']} 行"
            log_event(conn, "内部 API 生成号码结果", "internal_api", output_path.name, detail, actor="openclaw")
            conn.commit()

    return jsonify(
        _response_payload(
            mode="new-workbook",
            summary=summary,
            price_options=price_options,
            output_path=output_path,
            invalid_items=invalid_items,
            source_path=source_path,
            rows_limit=rows_limit,
            unmatched_limit=unmatched_limit,
        )
    )


def _run_file(payload: dict, *, export: bool):
    catalog, error_response = _load_catalog_or_error()
    if error_response:
        return error_response

    price_options, price_error = _parse_price_options(payload)
    if price_error:
        return _json_error(price_error)

    source_path, original_filename, source_error = _source_from_request(payload)
    if source_error or not source_path or not original_filename:
        return _json_error(source_error or "客户原始文件无效。")

    try:
        match_column = _parse_match_column(_payload_value(payload, "match_column", "column"))
    except ValueError as exc:
        return _json_error(str(exc))

    rows_limit = _parse_int(_payload_value(payload, "rows_limit"), default=200, minimum=0, maximum=1000)
    unmatched_limit = _parse_int(_payload_value(payload, "unmatched_limit"), default=100, minimum=0, maximum=1000)
    output_path = _source_output_path(payload, original_filename, source_path.suffix.lower()) if export else None

    try:
        summary = generate_excel_with_bld(
            source_path,
            output_path or (INTERNAL_OUTPUT_DIR / "__analysis.xlsx"),
            catalog,
            match_column=match_column,
            write_output=export,
            price_mode=price_options["price_mode"],
            exchange_rate=price_options["exchange_rate"],
        )
    except Exception as exc:
        return _json_error(
            f"分析客户原始文件失败：{exc}",
            422,
            column_preview=_preview_for_error(source_path),
        )

    if export and output_path:
        with connect(DB_PATH) as conn:
            detail = f"OpenClaw 增强客户原始文件 {summary['total']} 行，命中 {summary['matched']} 行，未找到 {summary['unmatched']} 行"
            log_event(conn, "内部 API 生成增强询价文件", "internal_api", output_path.name, detail, actor="openclaw")
            conn.commit()

    return jsonify(
        _response_payload(
            mode="augment-source-workbook",
            summary=summary,
            price_options=price_options,
            output_path=output_path,
            source_path=source_path,
            rows_limit=rows_limit,
            unmatched_limit=unmatched_limit,
        )
    )


def register(app) -> None:
    @app.post("/api/internal/inquiry/numbers")
    @internal_api_required
    def internal_inquiry_numbers():
        payload = _payload()
        export = _parse_bool(_payload_value(payload, "export"), default=False)
        return _run_numbers(payload, export=export)

    @app.post("/api/internal/inquiry/file")
    @internal_api_required
    def internal_inquiry_file():
        payload = _payload()
        export = _parse_bool(_payload_value(payload, "export"), default=False)
        return _run_file(payload, export=export)

    @app.post("/api/internal/inquiry/analyze")
    @internal_api_required
    def internal_inquiry_analyze():
        payload = _payload()
        if request.files or _payload_value(payload, "file_path", "path", "source_path"):
            return _run_file(payload, export=False)
        return _run_numbers(payload, export=False)
