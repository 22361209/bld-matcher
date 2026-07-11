from __future__ import annotations

from datetime import datetime


DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_text() -> str:
    return datetime.now().strftime(DATETIME_FORMAT)


def datetime_text(value: datetime) -> str:
    return value.strftime(DATETIME_FORMAT)
