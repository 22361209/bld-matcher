from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

from app.matcher import CatalogMatch, ProductCatalog, compact_text, normalize_code


@dataclass(frozen=True, slots=True)
class InquiryAdjustment:
    expected_bld_no: str = ""
    target_bld_no: str = ""
    tax_price: Decimal | None = None


def workbook_row_adjustment_key(sheet_number: int, row_number: int) -> str:
    """Return the stable identity of one physical workbook row."""
    if sheet_number < 1 or row_number < 1:
        raise ValueError("工作表和源行号必须从 1 开始。")
    return f"sheet:{sheet_number}:row:{row_number}"


def fragment_candidate_adjustment_key(source_key: str, candidate_number: int) -> str:
    """Give an expanded BLD fragment candidate a display-only key."""
    if not source_key or candidate_number < 1:
        raise ValueError("片段候选定位无效。")
    return f"{source_key}:fragment:{candidate_number}"


def parse_adjustments(value: object) -> dict[str, InquiryAdjustment]:
    if not isinstance(value, Mapping):
        raise ValueError("本次报价调整数据无效，请重新查询后再试。")
    parsed: dict[str, InquiryAdjustment] = {}
    for key, raw in value.items():
        if not isinstance(raw, Mapping):
            raise ValueError("本次报价调整数据无效，请重新查询后再试。")
        expected_bld_no = compact_text(raw.get("expected_bld_no"))
        target_bld_no = compact_text(raw.get("target_bld_no"))
        raw_price = raw.get("tax_price")
        tax_price = None
        if raw_price not in (None, ""):
            try:
                tax_price = Decimal(str(raw_price))
            except (InvalidOperation, ValueError):
                raise ValueError("报价含税单价必须是有效数字。") from None
            if not tax_price.is_finite() or tax_price < 0 or tax_price > Decimal("99999999.99"):
                raise ValueError("报价含税单价必须在 0 到 99999999.99 之间。")
            try:
                quantized_price = tax_price.quantize(Decimal("0.01"))
            except InvalidOperation:
                raise ValueError("报价含税单价必须是有效数字。") from None
            if tax_price != quantized_price:
                raise ValueError("报价含税单价最多保留两位小数。")
            tax_price = quantized_price
        if target_bld_no or tax_price is not None:
            if not expected_bld_no:
                raise ValueError("本次报价调整缺少原匹配产品，请重新查询后再试。")
            parsed[str(key)] = InquiryAdjustment(
                expected_bld_no=expected_bld_no,
                target_bld_no=target_bld_no,
                tax_price=tax_price,
            )
    return parsed


def ensure_adjustments_consumed(
    adjustments: Mapping[str, InquiryAdjustment] | None,
    consumed_keys: set[str],
) -> None:
    if adjustments and set(adjustments) != consumed_keys:
        raise ValueError("查询结果已变化，请重新查询后再调整产品或单价。")


def apply_adjustment(
    catalog: ProductCatalog,
    match: CatalogMatch | None,
    adjustment: InquiryAdjustment | None,
) -> tuple[CatalogMatch | None, Decimal | None, str]:
    if adjustment is None:
        return match, None, ""
    if match is None or " / " in match.bld_no:
        raise ValueError("只有唯一命中的产品可以进行本次报价调整。")
    if normalize_code(adjustment.expected_bld_no) != normalize_code(match.bld_no):
        raise ValueError("查询结果已变化，请重新查询后再调整产品或单价。")

    adjusted_match = match
    note = ""
    if adjustment.target_bld_no:
        target = catalog.by_bld.get(normalize_code(adjustment.target_bld_no))
        if target is None:
            raise ValueError(f"指定产品 {adjustment.target_bld_no} 不存在或已停用。")
        target_bld_no = compact_text(target.get("BLD NO."))
        if normalize_code(target_bld_no) != normalize_code(match.bld_no):
            adjusted_match = CatalogMatch(
                target_bld_no,
                match.score,
                match.reason,
                target,
                matched_codes=match.matched_codes,
                unmatched_codes=match.unmatched_codes,
            )
            note = f"本次报价指定：{match.bld_no} → {target_bld_no}"
    if adjustment.tax_price is not None:
        price_note = f"本次报价含税单价：¥{adjustment.tax_price:.2f}"
        note = f"{note}；{price_note}" if note else price_note
    return adjusted_match, adjustment.tax_price, note
