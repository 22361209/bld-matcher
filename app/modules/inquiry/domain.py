from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping

from app.matcher import (
    CatalogMatch,
    ProductCatalog,
    compact_text,
    normalize_code,
    psa_352x_key,
    split_codes,
)
from app.product_status import (
    format_product_status,
    product_status_language_for_price_mode,
)


PRICE_EXPORT_MODES = frozenset({"none", "tax", "net", "usd"})
PRICE_LABELS = {
    "none": "",
    "tax": "含税单价",
    "net": "不含税单价",
    "usd": "美金价",
}
BLD_FRAGMENT_MIN_LENGTH = 4
BLD_FRAGMENT_LIMIT = 80
QUICK_SEARCH_MIN_LENGTH = 4
QUICK_SEARCH_LIMIT = 80


class InquiryValidationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class CatalogUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PriceOptions:
    price_mode: str
    exchange_rate: float | None = None
    exchange_rate_text: str = ""

    def as_kwargs(self) -> dict[str, object]:
        return {
            "price_mode": self.price_mode,
            "exchange_rate": self.exchange_rate,
        }


@dataclass(frozen=True, slots=True)
class InquiryLimits:
    rows: int = 200
    unmatched: int = 100


@dataclass(frozen=True, slots=True)
class QuickCandidate:
    match: CatalogMatch
    match_type: str
    hit_code: str
    hit_label: str


def payload_value(payload: Mapping[str, object], *names: str, default=None):
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return value
    return default


def parse_bool(value: object, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def parse_limits(payload: Mapping[str, object]) -> InquiryLimits:
    return InquiryLimits(
        rows=parse_int(payload_value(payload, "rows_limit"), default=200, minimum=0, maximum=1000),
        unmatched=parse_int(
            payload_value(payload, "unmatched_limit"),
            default=100,
            minimum=0,
            maximum=1000,
        ),
    )


def parse_match_column(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        if value < 0:
            raise InquiryValidationError("inquiry.invalid_column", "match_column 不能小于 0。")
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
    raise InquiryValidationError(
        "inquiry.invalid_column",
        "match_column 需要传 0 起始列号，或 Excel 列字母，例如 A。",
    )


def parse_match_columns(payload: Mapping[str, object]) -> object:
    raw_columns = payload_value(payload, "match_columns", "columns")
    if raw_columns not in (None, ""):
        if isinstance(raw_columns, str):
            values = [part.strip() for part in re.split(r"[,，;；\s]+", raw_columns) if part.strip()]
        elif isinstance(raw_columns, (list, tuple)):
            values = list(raw_columns)
        else:
            values = [raw_columns]
        columns: list[int] = []
        seen: set[int] = set()
        for value in values:
            column = parse_match_column(value)
            if column is None or column in seen:
                continue
            seen.add(column)
            columns.append(column)
        return columns
    return parse_match_column(payload_value(payload, "match_column", "column"))


def parse_price_options(payload: Mapping[str, object], *, default: str = "tax") -> PriceOptions:
    mode = str(payload_value(payload, "price_mode", default=default) or default).strip().lower()
    if mode not in PRICE_EXPORT_MODES:
        raise InquiryValidationError(
            "inquiry.invalid_price_mode",
            "price_mode 仅支持 none、tax、net、usd。",
        )
    raw_rate = str(payload_value(payload, "exchange_rate", default="") or "").strip()
    exchange_rate = None
    if mode == "usd":
        try:
            exchange_rate = float(raw_rate)
        except ValueError as exc:
            raise InquiryValidationError(
                "inquiry.invalid_exchange_rate",
                "选择美金价时，请传有效 exchange_rate。",
            ) from exc
        if exchange_rate <= 0:
            raise InquiryValidationError(
                "inquiry.invalid_exchange_rate",
                "exchange_rate 必须大于 0。",
            )
    return PriceOptions(mode, exchange_rate, raw_rate)


def split_text_codes(text: str) -> list[str]:
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


def extract_numbers(payload: Mapping[str, object]) -> tuple[list[str], list[str]]:
    raw_numbers = payload_value(payload, "numbers", "codes", default=[])
    raw_text = payload_value(payload, "text", "query", default="")
    values: list[object] = []
    if isinstance(raw_numbers, str):
        values.extend(split_text_codes(raw_numbers))
    elif isinstance(raw_numbers, (list, tuple)):
        values.extend(raw_numbers)
    elif raw_numbers:
        values.append(raw_numbers)
    if raw_text:
        values.extend(split_text_codes(str(raw_text)))
    numbers: list[str] = []
    invalid: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if not normalize_code(text):
            invalid.append(text)
            continue
        numbers.append(text)
    return numbers, invalid


def _decimal_price(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def export_price(value: object, options: PriceOptions) -> int | float | None:
    price = _decimal_price(value)
    if price is None or options.price_mode == "none":
        return None
    if options.price_mode == "tax":
        return float(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if options.price_mode == "net":
        return int((price / Decimal("1.1")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if options.price_mode == "usd" and options.exchange_rate:
        rate = Decimal(str(options.exchange_rate))
        return float((price / Decimal("1.1") / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return None


def product_payload(row: dict | None) -> dict | None:
    if not row:
        return None
    image_paths = [
        compact_text(row.get(key))
        for key in ("image_path", "image_path_2", "image_path_3", "image_path_4", "image_path_5")
        if compact_text(row.get(key))
    ]
    return {
        "bld_no": compact_text(row.get("BLD NO.")),
        "series": row.get("SERIES") or "",
        "item": row.get("ITEM") or "",
        "oe_no_1": row.get("OE NO.1") or "",
        "oe_no_2": row.get("OE NO.2") or "",
        "models": row.get("Models") or "",
        "price_cny": row.get("price_cny"),
        "product_status": row.get("product_status") or "",
        "image_paths": image_paths,
    }


def attach_product_details(summary: dict, catalog: ProductCatalog) -> dict:
    rows = []
    for row in summary.get("rows", []):
        if row.get("product") or not row.get("bld_no") or " / " in str(row.get("bld_no") or ""):
            rows.append(row)
            continue
        product_row = catalog.by_bld.get(normalize_code(row.get("bld_no")))
        if product_row:
            row = dict(row)
            row["product"] = product_payload(product_row)
        rows.append(row)
    detailed = dict(summary)
    detailed["rows"] = rows
    return detailed


def bld_fragment_matches(catalog: ProductCatalog, query: object) -> list[dict]:
    key = normalize_code(query)
    if len(key) < BLD_FRAGMENT_MIN_LENGTH:
        return []
    matches = []
    for row in catalog.rows:
        bld_no = compact_text(row.get("BLD NO."))
        bld_key = normalize_code(bld_no)
        if not bld_key or key not in bld_key:
            continue
        matches.append(
            {
                "row": None,
                "oe": query,
                "name": "",
                "bld_no": bld_no,
                "price_cny": row.get("price_cny"),
                "product_status": row.get("product_status") or "",
                "reason": "BLD NO. 精准命中" if key == bld_key else "BLD NO. 片段命中",
                "score": 96 if key == bld_key else 86,
                "match_note": f"命中 BLD号：{bld_no}",
                "matched_oe_codes": [],
                "unmatched_oe_codes": [],
                "product": product_payload(row),
            }
        )
    return sorted(matches, key=lambda item: normalize_code(item["bld_no"]))[:BLD_FRAGMENT_LIMIT]


def looks_like_bld_shorthand(query: object) -> bool:
    key = normalize_code(query)
    if len(key) < BLD_FRAGMENT_MIN_LENGTH:
        return False
    return bool(re.fullmatch(r"\d{4}", key) or re.fullmatch(r"K\d{3,}[A-Z]*", key))


def bld_fragment_summary(catalog: ProductCatalog, query: object) -> dict:
    rows = bld_fragment_matches(catalog, query)
    if not rows:
        return {
            "total": 1,
            "matched": 0,
            "unmatched": 1,
            "rows": [
                {
                    "row": 2,
                    "oe": query,
                    "name": "",
                    "bld_no": "",
                    "price_cny": None,
                    "reason": "未找到",
                    "score": 0,
                    "match_note": "",
                    "matched_oe_codes": [],
                    "unmatched_oe_codes": [],
                }
            ],
        }
    for row in rows:
        row["row"] = 2
    return {"total": len(rows), "matched": len(rows), "unmatched": 0, "rows": rows}


def augment_summary_with_bld_fragments(summary: dict, catalog: ProductCatalog) -> dict:
    rows = []
    matched = 0
    unmatched = 0
    for row in summary.get("rows", []):
        if row.get("bld_no"):
            rows.append(row)
            matched += 1
            continue
        query = row.get("oe") or row.get("name") or ""
        fragment_rows = bld_fragment_matches(catalog, query)
        if not fragment_rows:
            rows.append(row)
            unmatched += 1
            continue
        for fragment_row in fragment_rows:
            fragment_row["row"] = row.get("row")
            rows.append(fragment_row)
            matched += 1
    augmented = dict(summary)
    augmented.update(rows=rows, matched=matched, unmatched=unmatched, total=matched + unmatched)
    return augmented


def format_rows(
    summary: dict,
    options: PriceOptions,
    limits: InquiryLimits,
) -> tuple[list[dict], list[str], bool]:
    formatted_rows = []
    unmatched_list = []
    for row in summary.get("rows", []):
        matched = bool(row.get("bld_no"))
        if not matched and len(unmatched_list) < limits.unmatched:
            unmatched_list.append(str(row.get("oe") or row.get("name") or "").strip())
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
                "product_status": format_product_status(
                    row.get("product_status"),
                    product_status_language_for_price_mode(options.price_mode),
                ),
                "export_price": export_price(row.get("price_cny"), options),
                "export_price_label": PRICE_LABELS.get(options.price_mode, ""),
                "matched_oe_codes": row.get("matched_oe_codes") or [],
                "unmatched_oe_codes": row.get("unmatched_oe_codes") or [],
                "product": row.get("product") or None,
            }
        )
    truncated = len(formatted_rows) > limits.rows
    return formatted_rows[: limits.rows], unmatched_list, truncated


def pasted_segment_codes(segment: str, catalog: ProductCatalog) -> list[str]:
    segment = segment.strip()
    if not segment or not normalize_code(segment):
        return []
    if re.search(r"\s+", segment) and catalog.match("", segment):
        return [segment]
    whitespace_codes = [part.strip() for part in re.split(r"\s+", segment) if normalize_code(part)]
    if len(whitespace_codes) > 1:
        return whitespace_codes
    return [segment]


def pasted_inquiry_codes(value: str, catalog: ProductCatalog) -> list[str]:
    text = value.strip()
    if not text:
        return []
    codes: list[str] = []
    for segment in re.split(r"[\n\r\t,，;；、/]+", text):
        codes.extend(pasted_segment_codes(segment, catalog))
    return codes


def should_render_pasted_result(query: str, codes: list[str]) -> bool:
    if len(codes) > 1:
        return True
    return bool(
        codes
        and re.search(r"\s+", query.strip())
        and normalize_code(codes[0]) == normalize_code(query)
    )


def _quick_match_type(reason: str) -> str:
    if "BLD" in reason:
        return "bld"
    if "品牌号码" in reason:
        return "brand"
    return "oe"


def _candidate_from_match(match: CatalogMatch) -> QuickCandidate:
    match_type = _quick_match_type(match.reason)
    label = {"bld": "BLD号", "brand": "品牌号"}.get(match_type, "OE号")
    hit_code = match.matched_codes[0] if match.matched_codes else match.bld_no
    return QuickCandidate(match, match_type, compact_text(hit_code), label)


def _add_quick_candidate(
    candidates: dict[str, QuickCandidate],
    *,
    query: str,
    row: dict,
    score: int,
    reason: str,
    match_type: str,
    hit_code: str,
    hit_label: str,
) -> None:
    bld_no = compact_text(row.get("BLD NO."))
    bld_key = normalize_code(bld_no)
    if not bld_key:
        return
    existing = candidates.get(bld_key)
    if existing and existing.match.score >= score:
        return
    candidates[bld_key] = QuickCandidate(
        CatalogMatch(bld_no, score, reason, row, matched_codes=(query,)),
        match_type,
        compact_text(hit_code) or compact_text(query),
        hit_label,
    )


def quick_candidate_matches(catalog: ProductCatalog, query: str) -> list[QuickCandidate]:
    key = normalize_code(query)
    if len(key) < QUICK_SEARCH_MIN_LENGTH:
        return []
    psa_probe = psa_352x_key(query)
    if psa_probe:
        psa_match = catalog.match("", query)
        if psa_match and ("PSA" in psa_match.reason or "3520/3521" in psa_match.reason):
            return [_candidate_from_match(psa_match)]
        if psa_probe[1]:
            return []
    candidates: dict[str, QuickCandidate] = {}
    for row, bld_no, bld_key, quick_codes in catalog.quick_search_rows:
        if key and bld_key:
            if key == bld_key:
                _add_quick_candidate(
                    candidates,
                    query=query,
                    row=row,
                    score=96,
                    reason="BLD NO. 精准命中",
                    match_type="bld",
                    hit_code=bld_no,
                    hit_label="BLD号",
                )
            elif key in bld_key:
                _add_quick_candidate(
                    candidates,
                    query=query,
                    row=row,
                    score=86,
                    reason="BLD NO. 片段命中",
                    match_type="bld",
                    hit_code=bld_no,
                    hit_label="BLD号",
                )
        for field, code, code_key in quick_codes:
            match_type = "brand" if field == "OE NO.2" else "oe"
            hit_label = "品牌号" if field == "OE NO.2" else "OE号"
            exact_reason = "品牌号码精准命中" if field == "OE NO.2" else "OE 精准命中"
            prefix_reason = "品牌号码前缀命中" if field == "OE NO.2" else "OE 前缀命中"
            partial_reason = "品牌号码片段命中" if field == "OE NO.2" else "OE 片段命中"
            if key == code_key:
                _add_quick_candidate(candidates, query=query, row=row, score=95, reason=exact_reason, match_type=match_type, hit_code=code, hit_label=hit_label)
            elif code_key.startswith(key):
                _add_quick_candidate(candidates, query=query, row=row, score=90, reason=prefix_reason, match_type=match_type, hit_code=code, hit_label=hit_label)
            elif key in code_key:
                _add_quick_candidate(candidates, query=query, row=row, score=82, reason=partial_reason, match_type=match_type, hit_code=code, hit_label=hit_label)
    for source_key, manual_bld in catalog.manual_map.items():
        row = catalog.by_bld.get(normalize_code(manual_bld))
        if not row:
            continue
        if key == source_key:
            _add_quick_candidate(candidates, query=query, row=row, score=100, reason="人工映射号码精准命中", match_type="oe", hit_code=source_key, hit_label="人工映射")
        elif source_key.startswith(key):
            _add_quick_candidate(candidates, query=query, row=row, score=90, reason="人工映射号码前缀命中", match_type="oe", hit_code=source_key, hit_label="人工映射")
        elif key in source_key:
            _add_quick_candidate(candidates, query=query, row=row, score=82, reason="人工映射号码片段命中", match_type="oe", hit_code=source_key, hit_label="人工映射")
    if not candidates:
        match = catalog.match("", query)
        if match:
            fallback = _candidate_from_match(match)
            _add_quick_candidate(
                candidates,
                query=query,
                row=match.row,
                score=match.score,
                reason=match.reason,
                match_type=fallback.match_type,
                hit_code=fallback.hit_code,
                hit_label=fallback.hit_label,
            )
    return sorted(
        candidates.values(),
        key=lambda candidate: (-candidate.match.score, normalize_code(candidate.match.bld_no)),
    )[:QUICK_SEARCH_LIMIT]


def quick_search(catalog: ProductCatalog | None, query: str) -> list[dict]:
    if not catalog:
        return []
    codes = split_codes(query)
    if not codes and query.strip():
        codes = [query.strip()]
    results = []
    for code in codes[:20]:
        key = normalize_code(code)
        if len(key) < QUICK_SEARCH_MIN_LENGTH:
            results.append({"query": code, "product": None, "reason": "请输入至少 4 位号码", "score": 0})
            continue
        matches = quick_candidate_matches(catalog, code)
        if matches:
            for candidate in matches:
                match = candidate.match
                row = match.row
                results.append(
                    {
                        "query": code,
                        "product": {
                            "bld_no": match.bld_no,
                            "series": row.get("SERIES", ""),
                            "item": row.get("ITEM", ""),
                            "oe_no_1": row.get("OE NO.1", ""),
                            "oe_no_2": row.get("OE NO.2", ""),
                            "models": row.get("Models", ""),
                            "price_cny": row.get("price_cny"),
                            "image_path": row.get("image_path", ""),
                            "image_path_2": row.get("image_path_2", ""),
                            "image_path_3": row.get("image_path_3", ""),
                            "image_path_4": row.get("image_path_4", ""),
                            "image_path_5": row.get("image_path_5", ""),
                        },
                        "reason": match.reason,
                        "score": match.score,
                        "match_type": candidate.match_type,
                        "hit_code": candidate.hit_code,
                        "hit_label": candidate.hit_label,
                    }
                )
        else:
            results.append({"query": code, "product": None, "reason": "未找到", "score": 0})
        if len(results) >= QUICK_SEARCH_LIMIT:
            break
    return results[:QUICK_SEARCH_LIMIT]
