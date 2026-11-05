from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


MATERIAL_STATUSES = frozenset({"active", "inactive", "all"})
MATERIAL_COLUMN_FILTER_FIELDS = frozenset(
    {
        "model",
        "code",
        "category",
        "car",
        "part",
        "material_grade",
        "spec_text",
        "pieces",
        "unit_weight",
        "net_weight",
        "scrap_weight",
        "scrap_percent",
        "active",
    }
)


def material_grade_for_code(code: object) -> str:
    return "SAPH440" if "440" in str(code or "") else "SPHC"


@dataclass(frozen=True, slots=True)
class MaterialPage:
    records: list[dict[str, object]]
    total: int
    limit: int
    offset: int
    stats: dict[str, int]


@dataclass(frozen=True, slots=True)
class MaterialImportResult:
    imported: int
    normalized: int
    stats: dict[str, object]


def normalize_status(value: object) -> str:
    status = str(value or "active").strip().lower()
    return status if status in MATERIAL_STATUSES else "active"


def normalize_column_filters(values: Mapping[str, object] | None) -> dict[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    for field, raw_values in (values or {}).items():
        if field not in MATERIAL_COLUMN_FILTER_FIELDS:
            continue
        sequence = raw_values if isinstance(raw_values, Sequence) and not isinstance(raw_values, str) else (raw_values,)
        selected = tuple(dict.fromkeys(str(value).strip() for value in sequence if str(value).strip()))
        if selected:
            normalized[field] = selected
    return normalized
