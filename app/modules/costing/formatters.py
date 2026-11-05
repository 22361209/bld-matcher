from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from zoneinfo import ZoneInfo


def cost_time(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            return moment.strftime("%Y-%m-%d %H:%M")
        return moment.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "—"


def format_cost_money(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        with localcontext() as context:
            context.prec = 64
            amount = Decimal(str(value))
            if not amount.is_finite():
                return "—"
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return format(amount if amount else Decimal(0), ",.2f")
    except (InvalidOperation, ValueError):
        return "—"
