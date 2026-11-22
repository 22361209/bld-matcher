"""Keep legacy weight snapshots and modern calculation inputs consistent in sync."""
from __future__ import annotations

import math
from collections.abc import Mapping

from app.modules.tubes.weights import normalize_weight_values


TUBE_WEIGHT_FIELDS = ("weight_mode", "weight_length_mm")
_LEGACY_WEIGHT_INPUTS = (
    "outer_diameter_mm", "inner_diameter_mm", "tolerance_mm", "consumption_mm", "weight_kg", "borrowed_from",
)
_AUTO_WEIGHT_INPUTS = {
    "outer_diameter_mm": "外径", "inner_diameter_mm": "内径", "tolerance_mm": "公差", "consumption_mm": "消耗",
}


def has_tube_weight_fields(rows: list[dict[str, object]]) -> bool:
    return any(any(field in row for field in TUBE_WEIGHT_FIELDS) for row in rows)


def prepare_tube_export(rows: list[dict[str, object]]) -> bool:
    """Default manual records remain readable by existing devices."""
    modern = any(row.get("weight_mode", "manual") != "manual" or row.get("weight_length_mm") is not None for row in rows)
    for row in rows:
        if modern:
            normalize_synced_tube(row, None)
        else:
            for field in TUBE_WEIGHT_FIELDS:
                row.pop(field, None)
    return modern


def normalize_synced_tube(incoming: dict[str, object], prior: Mapping[str, object] | None) -> None:
    modern = any(field in incoming for field in TUBE_WEIGHT_FIELDS)
    if modern:
        if any(field not in incoming for field in TUBE_WEIGHT_FIELDS):
            raise ValueError("管件计重字段不完整，请升级后重新导出。")
        normalized = normalize_weight_values(incoming)
        if normalized["weight_mode"] == "auto":
            for field, label in _AUTO_WEIGHT_INPUTS.items():
                value = incoming.get(field)
                if value is None or isinstance(value, str) and not value.strip():
                    incoming[field] = None
                    continue
                try:
                    number = float(str(value).strip())
                except (ValueError, OverflowError) as exc:
                    raise ValueError(f"管件{label}必须是有限数字。") from exc
                if isinstance(value, bool) or not math.isfinite(number):
                    raise ValueError(f"管件{label}必须是有限数字。")
                incoming[field] = number
            incoming.update(normalized)
            # Compute from exactly the numeric inputs that SQLite will persist.
            normalized = normalize_weight_values(incoming)
        incoming.update(normalized)
        return
    if prior is not None and prior.get("weight_mode") == "auto":
        if any(incoming.get(field) != prior.get(field) for field in _LEGACY_WEIGHT_INPUTS):
            raise ValueError(f"旧版业务包不能修改自动计重管件 {incoming['code']} 的计重资料，请升级来源系统后重新导出。")
    incoming["weight_mode"] = prior.get("weight_mode", "manual") if prior is not None else "manual"
    incoming["weight_length_mm"] = prior.get("weight_length_mm") if prior is not None else None
