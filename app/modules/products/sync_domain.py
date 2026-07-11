from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProductDiff:
    new_count: int
    updated_count: int
    conflict_count: int
    unchanged_count: int
    local_only_count: int
    rows: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class ProductSyncResult:
    new_count: int
    updated_count: int
    conflict_count: int
    unchanged_count: int
    deactivated_count: int
    copied_drawings: int = 0
    copied_images: int = 0
