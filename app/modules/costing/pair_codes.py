"""Recognize a single handed suffix in a BLD model number."""
from __future__ import annotations

import re

_HAND = re.compile(r"(?:^|(?<=[0-9-]))([LR])(?=[ABC](?:[0-9-]|$))", re.IGNORECASE | re.ASCII)


def _hand_in(code: str) -> re.Match[str] | None:
    matches = list(_HAND.finditer(code))
    return matches[0] if len(matches) == 1 else None


def code_side(code: str) -> str:
    match = _hand_in(code)
    return match[1].upper() if match else ""


def mirror_code(code: str) -> str:
    match = _hand_in(code)
    if not match:
        return ""
    hand = "R" if match[1].upper() == "L" else "L"
    return code[:match.start(1)] + hand + code[match.end(1):]


_PART_HAND = re.compile(r"([A-Z]+[0-9]{3,})([LR])(D?)", re.IGNORECASE | re.ASCII)


def mirror_part_code(code: str) -> str:
    # Component IDs also use e.g. B6004L and FA8121LD. Quantities such as
    # "10L" and generic identifiers such as "BOLT027" have no handed suffix.
    match = _PART_HAND.fullmatch(code)
    if match:
        side = "R" if match[2].upper() == "L" else "L"
        return match[1] + side + match[3]
    return mirror_code(code)
