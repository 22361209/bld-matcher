from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

from app.matcher import CatalogMatch, ProductCatalog, compact_text, normalize_code


@dataclass(frozen=True, slots=True)
class InquiryAdjustment:
    target_bld_no: str = ""
    tax_price: Decimal | None = None


def parse_adjustments(value: object) -> dict[str, InquiryAdjustment]:
    if not isinstance(value, Mapping):
        return {}
    parsed: dict[str, InquiryAdjustment] = {}
    for key, raw in value.items():
        if not isinstance(raw, Mapping):
            continue
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
            tax_price = tax_price.quantize(Decimal("0.01"))
        if target_bld_no or tax_price is not None:
            parsed[str(key)] = InquiryAdjustment(target_bld_no=target_bld_no, tax_price=tax_price)
    return parsed


def apply_adjustment(
    catalog: ProductCatalog,
    match: CatalogMatch | None,
    adjustment: InquiryAdjustment | None,
) -> tuple[CatalogMatch | None, Decimal | None, str]:
    if adjustment is None:
        return match, None, ""
    if match is None or " / " in match.bld_no:
        raise ValueError("只有唯一命中的产品可以进行本次报价调整。")

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
