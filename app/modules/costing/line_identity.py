"""Identify legacy carton specifications and part numbers written in cost item names."""
from __future__ import annotations

import re
from collections.abc import Mapping


_MEASURE = r"[0-9]+(?:\.[0-9]+)?"
_CARTON_SIZE = re.compile(
    rf"(?:(?P<label>纸箱|外箱)\s*[:：]?\s*)?"
    rf"(?P<size>{_MEASURE}(?:\s*[xX×*＊]\s*{_MEASURE}){{2}}(?:\s*(?:cm|mm|厘米|毫米))?)",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*")
# Long drawing numbers and the shorter seat, boot and bolt families used in the workbook.
_PART_NUMBER = re.compile(
    r"(?:[A-Z]{1,6}-?[0-9]{4,}[A-Z0-9]*(?:-[A-Z0-9]+)*"
    r"|[BCD]-[0-9]{3}(?:\.[0-9]+)?|(?:GD-?[0-9]{2}|[0-9]{2}[CD])-[0-9]+"
    r"|HT[0-9]{1,3}|BOLT[0-9]{3,4})",
    re.ASCII | re.IGNORECASE,
)
_STANDARD_NUMBER = re.compile(r"(?:GB[T]?|ISO|DIN|ASTM|SAE|JIS|M)-?[0-9]", re.IGNORECASE)


def inspect_line_identity(item: Mapping[str, object]) -> tuple[dict[str, str], str]:
    category = str(item.get("category") or "").strip()
    name = str(item.get("name") or "").strip()
    prior = str(item.get("product_code") or "").strip()
    size = _CARTON_SIZE.fullmatch(name)
    if name == "内外箱":
        return {}, "内外箱合计，请核对纸箱规格"
    carton_context = (
        category in {"外箱", "纸箱"}
        or name in {"外箱", "纸箱"}
        or (category == "外包装" and name == "外包装")
        or (size is not None and (size["label"] or category in {"外包装", "包装"} or item.get("mode") == "package"))
    )
    if carton_context:
        if size is None and name not in {"", "外箱", "纸箱", "外包装"}:
            return {}, "外箱项目名称含其他信息，请核对纸箱规格"
        fields = {"category": "外包装", "name": "纸箱"}
        if size:
            if prior and prior != size["size"]:
                return {}, "纸箱规格与已有产品编码不同"
            fields["product_code"] = size["size"]
        return {key: value for key, value in fields.items() if item.get(key) != value}, ""

    tokens = _TOKEN.findall(name)
    codes = {token for token in tokens if _PART_NUMBER.fullmatch(token) and not _STANDARD_NUMBER.match(token)}
    if not codes:
        return {}, ""
    if len(codes) > 1 or any(re.fullmatch(r"[0-9]{4,}", token) for token in tokens):
        return {}, "项目名称含多个可能的编号"
    code = next(iter(codes))
    if prior:
        return {}, "项目料号与已有产品编码不同" if prior != code else ""
    return {"product_code": code}, ""


def line_identity_changes(item: Mapping[str, object]) -> dict[str, str]:
    return inspect_line_identity(item)[0]
