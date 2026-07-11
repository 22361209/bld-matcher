from __future__ import annotations

from dataclasses import dataclass


MATERIAL_STATUSES = frozenset({"active", "inactive", "all"})


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
