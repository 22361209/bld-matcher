from __future__ import annotations

from app.bld_sort import bld_sort_key_fn

# 同型号不同状态（如 K8053LA / K8053LB）仅按末尾单个 A-E 字母区分；
# 左右侧别 L/R 与 "-1" 等变体不纳入同组。
VARIANT_STATUS_LETTERS = frozenset("ABCDE")


def _compact(value: object) -> str:
    return str(value or "").strip()


def variant_base(bld_no: object) -> tuple[str, str] | None:
    """Split a BLD number into (base, status letter) when it ends with A-E."""
    text = _compact(bld_no).upper()
    if len(text) < 2:
        return None
    letter = text[-1]
    if letter not in VARIANT_STATUS_LETTERS:
        return None
    return text[:-1], letter


def build_variant_groups(rows: list[dict]) -> dict[str, tuple[dict, ...]]:
    """Group catalog rows that differ only by a trailing status letter (A-E)."""
    by_base: dict[str, dict[str, dict]] = {}
    for row in rows:
        parsed = variant_base(row.get("BLD NO."))
        if not parsed:
            continue
        base, letter = parsed
        by_base.setdefault(base, {}).setdefault(letter, row)

    groups: dict[str, tuple[dict, ...]] = {}
    for base, by_letter in by_base.items():
        if len(by_letter) < 2:
            continue
        members = sorted(
            by_letter.values(),
            key=lambda row: bld_sort_key_fn(_compact(row.get("BLD NO."))),
        )
        groups[base] = tuple(members)
    return groups


def default_variant(members: list[dict] | tuple[dict, ...]) -> dict:
    """Pick the default status variant: letter A (带球头) first."""
    for row in members:
        parsed = variant_base(row.get("BLD NO."))
        if parsed and parsed[1] == "A":
            return row
    for row in members:
        if "球头" in str(row.get("product_status") or ""):
            return row
    return members[0]
