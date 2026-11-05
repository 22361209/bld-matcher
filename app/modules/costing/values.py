"""Bounded numeric and text inputs shared across cost calculations."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


MAX_VALUE = Decimal("1000000000000")
FIELD_LABELS = {
    "amount": "金额", "unit_price": "单价", "quantity": "数量", "weight": "重量",
    "processing_fee": "加工费", "length": "长", "width": "宽", "height": "高",
    "strap_rate": "打包带单价", "approved_cost": "核定成本", "scrap_credit": "废料抵减额",
    "theoretical_scrap": "理论废料金额", "suggested_tax_price_override": "建议含税单价",
    "scrap_unit_price": "废料单价", "scrap_credit_override": "手动实际抵减额",
    "gross_weight": "单件下料重量", "net_weight": "净重", "scrap_weight": "单件废料重量", "pieces": "下料只数",
}


class CostingValidationError(ValueError):
    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = "costing.invalid"


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    exponent = value.as_tuple().exponent
    return format(value, "f").rstrip("0").rstrip(".") if isinstance(exponent, int) and exponent < 0 else format(value, "f")


def text(value: object, field: str, limit: int = 240) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CostingValidationError(f"{field} 必须是文字。", field)
    result = value.strip()
    if len(result) > limit:
        raise CostingValidationError(f"{field} 最多允许 {limit} 个字符。", field)
    return result


def number(value: object, field: str, *, allow_negative: bool = False) -> Decimal | None:
    if value is None or value == "":
        return None
    label = FIELD_LABELS.get(field.split(".")[-1], field)
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise CostingValidationError(f"{label} 必须是数字。", field)
    if isinstance(value, str) and len(value) > 120:
        raise CostingValidationError(f"{label} 数值或精度超出范围。", field)
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise CostingValidationError(f"{label} 必须是数字。", field) from exc
    if not parsed.is_finite():
        raise CostingValidationError(f"{label} 必须是有限数字。", field)
    if not allow_negative and parsed < 0:
        raise CostingValidationError(f"{label} 不能为负数。", field)
    exponent = parsed.as_tuple().exponent
    if (parsed.copy_abs() > MAX_VALUE or not isinstance(exponent, int)
            or exponent < -18 or len(parsed.as_tuple().digits) > 31):
        raise CostingValidationError(f"{label} 数值或精度超出范围。", field)
    return parsed
