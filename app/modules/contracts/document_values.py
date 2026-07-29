from __future__ import annotations

from decimal import Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP, localcontext


MONEY_QUANT = Decimal("0.01")
MAX_DECIMAL_ADJUSTED_EXPONENT = 12
MAX_CONTRACT_AMOUNT = Decimal("999999999999999.99")


def _text(value: object) -> str:
    return str(value or "").strip()


def _money(value: Decimal) -> str:
    return f"{value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP):,.2f}"


def _quantity(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _rmb_upper(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("金额必须是有限数字。")
    if value > MAX_CONTRACT_AMOUNT:
        raise ValueError("金额数值过大。")
    value = value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if value < 0:
        raise ValueError("金额不能为负数。")
    digits = "零壹贰叁肆伍陆柒捌玖"
    units = ["", "拾", "佰", "仟"]
    sections = ["", "万", "亿", "兆"]

    def section_to_upper(number: int) -> str:
        result = ""
        zero_pending = False
        for index in range(4):
            digit = number % 10
            if digit:
                if zero_pending:
                    result = digits[0] + result
                    zero_pending = False
                result = digits[digit] + units[index] + result
            elif result:
                zero_pending = True
            number //= 10
        return result

    yuan = int(value)
    fraction = int((value - Decimal(yuan)) * 100)
    if yuan == 0:
        yuan_text = "零元"
    else:
        parts = []
        section_index = 0
        zero_pending = False
        while yuan:
            section = yuan % 10000
            if section:
                prefix = digits[0] if zero_pending and parts else ""
                parts.append(prefix + section_to_upper(section) + sections[section_index])
                zero_pending = section < 1000
            elif parts:
                zero_pending = True
            yuan //= 10000
            section_index += 1
        yuan_text = "".join(reversed(parts)) + "元"
    jiao, fen = divmod(fraction, 10)
    if not fraction:
        return yuan_text + "整"
    fraction_text = ""
    if jiao:
        fraction_text += digits[jiao] + "角"
    elif yuan:
        fraction_text += "零"
    if fen:
        fraction_text += digits[fen] + "分"
    return yuan_text + fraction_text


def _contract_line_amount(quantity: Decimal, unit_price: Decimal, label: str) -> Decimal:
    try:
        with localcontext() as context:
            context.prec = 64
            amount = quantity * unit_price
            if not amount.is_finite():
                raise ValueError(f"{label}金额必须是有限数字。")
            if amount > MAX_CONTRACT_AMOUNT:
                raise ValueError(f"{label}金额数值过大。")
            return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise ValueError(f"{label}金额数值过大。") from exc


def _contract_total_amount(current: Decimal, amount: Decimal) -> Decimal:
    try:
        with localcontext() as context:
            context.prec = 64
            total = current + amount
            if not total.is_finite():
                raise ValueError("合同合计金额必须是有限数字。")
            if total > MAX_CONTRACT_AMOUNT:
                raise ValueError("合同合计金额数值过大。")
            return total
    except DecimalException as exc:
        raise ValueError("合同合计金额数值过大。") from exc


def _parse_decimal(
    value: object,
    label: str,
    *,
    positive: bool = False,
    allow_zero: bool = True,
) -> Decimal:
    text = _text(value)
    if not text:
        raise ValueError(f"{label}不能为空。")
    try:
        number = Decimal(text.replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{label}必须是数字：{text}") from exc
    if not number.is_finite():
        raise ValueError(f"{label}必须是有限数字。")
    if number and number.adjusted() > MAX_DECIMAL_ADJUSTED_EXPONENT:
        raise ValueError(f"{label}数值过大。")
    if positive and number <= 0:
        raise ValueError(f"{label}必须大于 0。")
    if not allow_zero and number == 0:
        raise ValueError(f"{label}不能为 0。")
    if number < 0:
        raise ValueError(f"{label}不能为负数。")
    return number
