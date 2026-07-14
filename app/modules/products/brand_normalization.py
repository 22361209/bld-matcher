from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrandNormalizationChange:
    product_id: int
    bld_no: str
    before: str
    after: str

    def web_payload(self) -> dict[str, object]:
        return {
            "product_id": self.product_id,
            "bld_no": self.bld_no,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class BrandNormalizationResult:
    changed_count: int
    backup_path: Path


@dataclass(frozen=True)
class BrandNormalizationPreview:
    changes: tuple[BrandNormalizationChange, ...]
    digest: str

    @property
    def changed_count(self) -> int:
        return len(self.changes)


class BrandNormalizationConflictError(RuntimeError):
    pass


class BrandNormalizationPreviewChangedError(RuntimeError):
    def __init__(self, expected: str, actual: str) -> None:
        super().__init__("品牌清洗预览已变化，请刷新预览后再执行。")
        self.expected = expected
        self.actual = actual


def build_brand_normalization_preview(
    changes: Sequence[BrandNormalizationChange],
) -> BrandNormalizationPreview:
    stable_changes = tuple(changes)
    payload = [change.web_payload() for change in stable_changes]
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return BrandNormalizationPreview(changes=stable_changes, digest=digest)


_LEGACY_COMPOUND_BRANDS: dict[str, tuple[str, ...]] = {
    "DODGE CHRYSLER": ("DODGE", "CHRYSLER"),
    "DODGE RAM": ("DODGE", "RAM"),
    "JEEP CHRYSLER": ("JEEP", "CHRYSLER"),
    "JEEP DODGE": ("JEEP", "DODGE"),
    "MG ROEWE": ("MG", "ROEWE"),
}

_LEGACY_BRAND_ALIASES = {
    "MAZADA": "MAZDA",
    "RAM": "DODGE",
    "RAM TRUCKS": "DODGE",
}


def _compact_line(value: str) -> str:
    return " ".join(value.split()).strip().upper()


def _source_lines(value: object) -> list[str]:
    if value is None:
        return []
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return [line for raw_line in text.split("\n") if (line := _compact_line(raw_line))]


def _join_legacy_split_names(lines: list[str]) -> list[str]:
    joined: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index] == "MERCEDES-" and index + 1 < len(lines) and lines[index + 1] == "BENZ":
            joined.append("MERCEDES-BENZ")
            index += 2
            continue
        joined.append(lines[index])
        index += 1
    return joined


def _expand_brand_line(line: str) -> tuple[str, ...]:
    legacy_brands = _LEGACY_COMPOUND_BRANDS.get(line)
    if legacy_brands is not None:
        return legacy_brands
    if "/" in line:
        return tuple(part for raw_part in line.split("/") if (part := _compact_line(raw_part)))
    return (line,)


def canonicalize_brands(value: object) -> str:
    """Return a stable, uppercase, one-brand-per-line catalog value.

    Only confirmed legacy compound spellings are split on spaces. This keeps
    legitimate multi-word brands such as ``GREAT WALL`` intact. Slash-delimited
    values are treated as multiple brands, and the historical ``RAM`` token is
    folded into ``DODGE``.
    """

    normalized: list[str] = []
    seen: set[str] = set()
    for line in _join_legacy_split_names(_source_lines(value)):
        for brand in _expand_brand_line(line):
            canonical = _LEGACY_BRAND_ALIASES.get(brand, brand)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            normalized.append(canonical)
    return "\n".join(normalized)
