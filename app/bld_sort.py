from __future__ import annotations

import re
from functools import cmp_to_key
from typing import Any


FIRST_NUMBER_RE = re.compile(r"\d+")
NATURAL_TOKEN_RE = re.compile(r"\d+|\D+")


def _natural_tokens(value: str) -> tuple[tuple[int, int | str], ...]:
    tokens: list[tuple[int, int | str]] = []
    for token in NATURAL_TOKEN_RE.findall(value):
        if token.isdigit():
            tokens.append((0, int(token)))
        else:
            tokens.append((1, token))
    return tuple(tokens)


def bld_sort_key(value: Any) -> tuple:
    text = str(value or "").strip().upper()
    first_number = FIRST_NUMBER_RE.search(text)
    if not first_number:
        return (_natural_tokens(text), -1, (), 0, (), text)

    prefix = text[: first_number.start()]
    number = int(first_number.group())
    suffix = text[first_number.end() :]

    side_order = 0
    variant = suffix
    if suffix.startswith(("L", "R")):
        side_order = 1 if suffix[0] == "L" else 2
        variant = suffix[1:]

    return (
        _natural_tokens(prefix),
        number,
        _natural_tokens(variant),
        side_order,
        _natural_tokens(suffix),
        text,
    )


def compare_bld_no(left: Any, right: Any) -> int:
    left_key = bld_sort_key(left)
    right_key = bld_sort_key(right)
    return (left_key > right_key) - (left_key < right_key)


bld_sort_key_fn = cmp_to_key(compare_bld_no)
