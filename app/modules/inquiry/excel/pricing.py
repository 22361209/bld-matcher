from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.product_status import format_product_status, product_status_language_for_price_mode


PRICE_EXPORT_MODES = {"none", "tax", "net", "usd"}


def price_export_header(price_mode: str) -> str:
    if price_mode == "tax":
        return "含税单价"
    if price_mode == "net":
        return "不含税单价"
    if price_mode == "usd":
        return "美金价"
    return ""


def decimal_price(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def numeric_price(value: object) -> float | None:
    price = decimal_price(value)
    if price is None:
        return None
    return float(price)


def match_export_price(match, price_mode: str, exchange_rate: float | None) -> float | int | None:
    if price_mode not in {"tax", "net", "usd"} or not match:
        return None
    if " / " in (match.bld_no or ""):
        return None

    raw_price = match.row.get("price_cny")
    price = decimal_price(raw_price)
    if price is None:
        return None
    if price_mode == "tax":
        return round(float(price), 2)
    if price_mode == "net":
        net_price = (price / Decimal("1.1")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
        return int(net_price)
    if not exchange_rate or exchange_rate <= 0:
        return None
    return round(float(price) / 1.1 / exchange_rate, 2)


def match_export_status(match, price_mode: str) -> str:
    if price_mode not in {"tax", "net", "usd"} or not match or " / " in (match.bld_no or ""):
        return ""
    return format_product_status(
        match.row.get("product_status"),
        product_status_language_for_price_mode(price_mode),
    )
