from __future__ import annotations

from datetime import datetime
from math import isfinite


ASSEMBLY = "焊接件"
STAMP = "冲压件"
PLATE = "板料"
TUBE_KINDS = ("焊接管", "焊接座管", "轴类")
KINDS = (ASSEMBLY, STAMP, *TUBE_KINDS)
PART_FIELDS = ("code", "name", "model", "kind", "plate", "pieces", "net_weight", "grade", "thickness", "width", "length")


def revision_time():
    return datetime.now().isoformat(sep=" ", timespec="microseconds")


def positive(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是正数。") from None
    if not isfinite(number) or not 0 < number <= 1e12:
        raise ValueError(f"{label}超出有效范围。")
    return number


def exact_spec(row):
    return "×".join(str(float(row[k])).removesuffix(".0") for k in ("thickness", "width", "length"))


def part(conn, code):
    row = conn.execute("SELECT * FROM bom_parts WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None
