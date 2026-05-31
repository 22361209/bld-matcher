from __future__ import annotations

import re


STATUS_LANGUAGE_ZH = "zh"
STATUS_LANGUAGE_EN = "en"

_STATUS_TERMS = {
    "球头": ("ball joint", "ball joints"),
    "衬套": ("bushing", "bushings"),
    "橡胶垫": ("rubber pad", "rubber pads"),
}
_STATUS_TOKEN_RE = re.compile(r"(\d+)\s*个?\s*(" + "|".join(re.escape(term) for term in _STATUS_TERMS) + r")")


def product_status_language_for_price_mode(price_mode: str) -> str:
    return STATUS_LANGUAGE_EN if price_mode == "usd" else STATUS_LANGUAGE_ZH


def product_status_header_for_price_mode(price_mode: str) -> str:
    return "Product Status" if product_status_language_for_price_mode(price_mode) == STATUS_LANGUAGE_EN else "产品状态"


def format_product_status(value: object, language: str = STATUS_LANGUAGE_EN) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if language != STATUS_LANGUAGE_EN:
        return text

    lines = []
    for line in re.split(r"[\r\n]+", text):
        line = line.strip()
        if not line:
            continue
        pieces = []
        cursor = 0
        matched = False
        for match in _STATUS_TOKEN_RE.finditer(line):
            unmatched = line[cursor : match.start()].strip()
            if unmatched:
                pieces.append(unmatched)
            count = int(match.group(1))
            singular, plural = _STATUS_TERMS[match.group(2)]
            pieces.append(f"{count} {singular if count == 1 else plural}")
            cursor = match.end()
            matched = True
        tail = line[cursor:].strip()
        if tail:
            pieces.append(tail)
        lines.append(" ".join(pieces) if matched else line)
    return "\n".join(lines)
