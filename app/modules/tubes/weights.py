"""Single-tube material consumption, using the source workbook's steel coefficient."""
from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal, DecimalException


WEIGHT_MODES = frozenset({"manual", "auto"})
MANUAL_WEIGHT_CODES = ("KG6028",)
WEIGHT_INPUTS = {
    "outer_diameter_mm": "外径",
    "inner_diameter_mm": "内径",
    "weight_length_mm": "计重长度",
    "tolerance_mm": "公差",
    "consumption_mm": "消耗",
}


def _number(value: object, label: str, *, positive: bool = False) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是有效数字。")
    try:
        result = Decimal(str(value).strip())
        valid = result.is_finite() and math.isfinite(float(result))
    except (DecimalException, ValueError, OverflowError):
        valid = False
        result = Decimal(0)
    if not valid:
        raise ValueError(f"{label}必须是有限数字。")
    if result < 0 or (positive and result == 0):
        raise ValueError(f"{label}必须{'大于零' if positive else '大于或等于零'}。")
    return result


def calculate_single_weight(data: Mapping[str, object]) -> float | None:
    """All lengths are mm. Missing inputs stay unknown; explicit zero is retained."""
    values = {
        key: _number(data.get(key), label, positive=key in {"outer_diameter_mm", "weight_length_mm"})
        for key, label in WEIGHT_INPUTS.items()
    }
    outer, inner = values["outer_diameter_mm"], values["inner_diameter_mm"]
    if outer is not None and inner is not None and outer <= inner:
        raise ValueError("外径必须大于内径。")
    if any(value is None for value in values.values()):
        return None
    assert outer is not None and inner is not None
    length, tolerance, consumption = (values[key] for key in ("weight_length_mm", "tolerance_mm", "consumption_mm"))
    assert length is not None and tolerance is not None and consumption is not None
    try:
        weight = (outer * outer - inner * inner) / 4 * Decimal("0.02466") * (length + tolerance + consumption) / 1000
        result = float(weight)
    except (DecimalException, OverflowError) as exc:
        raise ValueError("计重结果超出可用范围，请核对尺寸和单位。") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError("计重结果超出可用范围，请核对尺寸和单位。")
    return result


def normalize_weight_values(
    data: Mapping[str, object], existing: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Normalize persistence inputs without guessing a length or modifying a legacy weight."""
    merged = {**(existing or {}), **data}
    mode = str(merged.get("weight_mode", "manual") or "").strip()
    if mode not in WEIGHT_MODES:
        raise ValueError("请选择有效的重量计算方式。")
    code = str(merged.get("code") or "").strip().upper()
    if mode == "auto" and code in MANUAL_WEIGHT_CODES:
        raise ValueError(f"{code} 使用特殊计重公式，暂保留手工重量，不能启用通用自动计重。")
    length = _number(merged.get("weight_length_mm"), "计重长度", positive=True)
    if mode == "auto":
        weight = None if str(merged.get("borrowed_from") or "").strip() else calculate_single_weight(merged)
    else:
        manual_weight = _number(merged.get("weight_kg"), "重量")
        weight = float(manual_weight) if manual_weight is not None else None
    return {"weight_mode": mode, "weight_length_mm": float(length) if length is not None else None, "weight_kg": weight}
