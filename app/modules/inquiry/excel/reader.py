from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
import re
from string import ascii_uppercase
from typing import Any, cast


from app.matcher import normalize_code, split_codes


INQUIRY_HEADERS = {
    "name": {"物料名称", "产品名称", "名称", "ITEM", "PART"},
    "oe": {
        "OE号",
        "OE",
        "OE NO",
        "OE NO.",
        "OE号码",
        "OE REFERENCE",
        "OE REF",
        "号码",
        "查询号码",
        "客户号码",
        "BLD号",
        "BLD NO",
        "BLD NO.",
    },
    "description": {"物料描述", "描述", "DESCRIPTION", "DESC"},
}


def _norm_header(value: object) -> str:
    return str(value or "").strip().upper().replace(" ", "")


MANUAL_HEADER_ALIASES = {_norm_header(alias) for aliases in INQUIRY_HEADERS.values() for alias in aliases} | {
    _norm_header(alias)
    for alias in {
        "SN",
        "NO",
        "NO.",
        "序号",
        "编号",
        "数量",
        "客户号码",
        "客户编码",
        "号码",
        "料号",
        "客户料号",
        "客户参考",
        "参考号",
        "品牌号码",
        "OEM",
        "CODE",
        "PART NUMBER",
        "PART CODE",
        "REFERENCE",
        "ITEM NO",
        "ITEM NO.",
        "PART NO",
        "PART NO.",
        "QTY",
        "QUANTITY",
        "رقم",
        "كمية",
    }
}


def _looks_like_match_code(value: object, *, after_header_candidate: bool = False) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    text = str(value or "").strip()
    if not text:
        return False
    if re.fullmatch(r"[A-Za-z]+(?:[-_./][A-Za-z]+)+", text):
        return True
    if after_header_candidate and text.isascii() and text.isalpha():
        return True
    upper = text.upper()
    if re.search(r"[A-Z]{3,}\s+[A-Z]{3,}", upper):
        return False
    parts = split_codes(text)
    for part in parts:
        key = normalize_code(part)
        if any(char.isdigit() for char in key) and (len(key) >= 5 or part.isascii() and part.isalnum()):
            return True
    return False


def _is_manual_header_label(value: object) -> bool:
    label = _norm_header(value)
    return label.replace("-", "").replace("_", "") in MANUAL_HEADER_ALIASES or bool(
        re.fullmatch(r"OEM?(?:NO\.?|号)?[1-9]\d?", label) or re.fullmatch(r"(?:OE|OEM|REF)(?:/(?:OE|OEM|REF))+", label)
    )


def _is_cover_title(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.search(
        r"询价|报价|清单|订购单|采购单|采购订单|^\s*(?:使用说明|填写说明|说明|须知)|"
        r"\b(?:inquiry|enquiry|rfq|quotation|quote|request|order|listing|list|instructions?)\b|"
        r"[a-z]{3,}\s.*(?:19|20)\d\d[-./]\d{1,2}\b",
        value, re.IGNORECASE,
    ))


def _find_inquiry_columns(sheet) -> tuple[int, dict[str, int]]:
    aliases = {_norm_header(alias): key for key, names in INQUIRY_HEADERS.items() for alias in names}
    for row_index in range(min(sheet.nrows, 20)):
        columns: dict[str, int] = {}
        for col_index in range(sheet.row_len(row_index)):
            key = aliases.get(_norm_header(sheet.cell_value(row_index, col_index)))
            if key:
                columns[key] = col_index
        if "oe" in columns:
            return row_index, columns
    raise ValueError("询价表没有找到可识别表头，需要包含 OE号。")


def _column_label(index: int) -> str:
    label = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        label = ascii_uppercase[remainder] + label
    return label


def has_source_value(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _norm_openpyxl_cell(value: object) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _manual_match_columns(match_column: object) -> list[int]:
    if match_column is None:
        return []
    if isinstance(match_column, int | str):
        values = [match_column]
    elif isinstance(match_column, Iterable):
        values = list(match_column)
    else:
        values = [match_column]

    columns: list[int] = []
    seen = set()
    for value in values:
        try:
            index = int(cast(Any, value))
        except (TypeError, ValueError):
            continue
        if index < 0 or index in seen:
            continue
        seen.add(index)
        columns.append(index)
    return columns


def _combined_match_text(match_values: list[tuple[str, object]]) -> str:
    return "\n".join(str(value).strip() for _, value in match_values if str(value or "").strip())


def _selected_match_row_is_header(match_values: list[tuple[str, object]]) -> bool:
    values = [value for _, value in match_values if str(value or "").strip()]
    if not values:
        return False
    return all(_is_manual_header_label(value) for value in values)


def _xls_match_values(
    sheet,
    row_index: int,
    match_columns: list[int],
) -> list[tuple[str, object]]:
    values = []
    for column_index in match_columns:
        if 0 <= column_index < sheet.ncols:
            values.append((_column_label(column_index), _xls_cell_value(sheet, row_index, column_index)))
    return values


def _xls_cell_value(sheet, row_index: int, col_index: int) -> object:
    return sheet.cell_value(row_index, col_index) if col_index < sheet.row_len(row_index) else ""


def _xlsx_match_values(
    sheet,
    row_index: int,
    match_columns: list[int],
    used_columns: Collection[int],
) -> list[tuple[str, object]]:
    values = []
    for column_index in match_columns:
        if column_index in used_columns:
            values.append((_column_label(column_index - 1), sheet.cell(row_index, column_index).value))
    return values


def _cell_from_values(row: Sequence, column_index: int) -> object:
    return row[column_index - 1] if 0 < column_index <= len(row) else ""


def _customer_code_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _row_match_values(
    values: tuple,
    match_columns: list[int],
) -> list[tuple[str, object]]:
    return [
        (_column_label(column_index - 1), _cell_from_values(values, column_index)) for column_index in match_columns
    ]


def _find_xlsx_inquiry_columns_from_rows(rows: Sequence[Sequence]) -> tuple[int, dict[str, int]]:
    aliases = {_norm_header(alias): key for key, names in INQUIRY_HEADERS.items() for alias in names}
    for row_index, row in enumerate(rows[:20], start=1):
        columns: dict[str, int] = {}
        for col_index, value in enumerate(row, start=1):
            key = aliases.get(_norm_openpyxl_cell(value))
            if key:
                columns[key] = col_index
        if "oe" in columns:
            return row_index, columns
    raise ValueError("询价表没有找到可识别表头，需要包含 OE号。")
