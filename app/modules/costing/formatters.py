from __future__ import annotations

from datetime import datetime
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
