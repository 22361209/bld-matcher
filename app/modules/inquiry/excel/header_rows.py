from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from datetime import date
import re

from .reader import _is_cover_title, _is_manual_header_label, _looks_like_match_code, has_source_value


def validate_header_rows(overrides: dict[int, int] | None, sheet_count: int, match_columns: Sequence[int]) -> None:
    for sheet, row in (overrides or {}).items():
        if type(sheet) is not int or not 1 <= sheet <= sheet_count:
            raise ValueError(f"第 {sheet} 张工作表不存在，请重新选择表头。")
        if type(row) is not int or row < 0:
            raise ValueError("表头行号应为正整数，无表头请选择 0。")
        if not match_columns:
            raise ValueError("指定表头时请选择匹配列。")


def _cover(values: Iterable[object]) -> bool:
    populated = [value for value in values if has_source_value(value)]
    return len(populated) == 1 and _is_cover_title(populated[0])


_SOURCE_HEADER_FIELDS = {
    label: field
    for field, labels in {
        "name": ("中文名称", "英文名称", "产品名称", "物料名称", "品名", "名称", "DESCRIPTION", "ITEM NAME"),
        "price": ("含税价", "不含税价", "含税单价", "不含税单价", "单价", "价格", "UNIT PRICE", "PRICE"),
        "minimum": ("起订量", "最小起订量", "最小订购量", "MOQ"),
        "quantity": ("数量", "采购数量", "订购数量", "QTY", "QUANTITY"),
        "code": ("编号", "产品编码", "客户编码", "客户料号", "OE号", "OE", "OEM", "PART NO.", "PART NUMBER"),
        "remark": ("备注", "说明", "REMARKS"),
    }.items()
    for label in labels
}


def _source_header_field(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    label = re.split(r"[（(\n]", value, maxsplit=1)[0].strip().upper()
    return _SOURCE_HEADER_FIELDS.get(label)


def _is_data_or_title(value: object) -> bool:
    return has_source_value(value) and (
        not isinstance(value, str) or _looks_like_match_code(value) or _is_cover_title(value)
    )


def _is_source_header(values: Iterable[object]) -> bool:
    fields = set()
    for value in values:
        if not has_source_value(value):
            continue
        field = _source_header_field(value)
        if field:
            fields.add(field)
        elif _is_data_or_title(value):
            return False
    return len(fields) >= 2


def _invalid_header(sheet_name: str, row: int) -> ValueError:
    return ValueError(f"工作表「{sheet_name}」的第 {row} 行不存在或没有内容，请核对表头行号。")


def _cover_note_label(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.match(
        r"\s*(?:电话|联系电话|联系人|联系地址|出具日期|日期|备注|附言|编制|制表|"
        r"(?:tel|telephone|phone|contact|date|ref|reference|remark|note)(?:\s|:|：|$))",
        value, re.IGNORECASE,
    ))


def _date_value(value: object) -> bool:
    return isinstance(value, date) or (
        isinstance(value, str) and bool(re.fullmatch(
            r"\s*(?:19|20)\d\d[-./年]\d{1,2}(?:[-./月]\d{1,2}日?)?\s*", value
        ))
    )


def _select_header(candidates: Iterable[tuple[int, list, str]], *, headerless: bool = False) -> int | None:
    """Return the physical header row, zero for headerless input, or None for a sheet without inquiries."""
    candidate = first_code_header = None
    cover = False
    for number, values, kind in candidates:
        if headerless:
            return 0
        if kind == "source_header":
            return number
        if kind == "cover":
            cover = True
            continue
        if kind == "note":
            if any(_is_manual_header_label(value) for value in values):
                return number
            continue
        if not values:
            continue
        if cover and all(_date_value(value) for value in values):
            continue
        if any(
            not _is_manual_header_label(value)
            and _looks_like_match_code(value, after_header_candidate=candidate is not None)
            for value in values
        ):
            if not cover:
                return candidate or 0
            if first_code_header is None:
                first_code_header = candidate if candidate is not None else number - 1
        elif any(_is_manual_header_label(value) for value in values):
            return number
        elif candidate is None:
            candidate = number
    return first_code_header


def header_candidates(rows: Iterable[Sequence], selected: Sequence[int]) -> Iterator[tuple[int, list, str]]:
    first = True
    cover = False
    for number, row in enumerate(rows, start=1):
        if not any(has_source_value(value) for value in row):
            continue
        values = [row[col] for col in selected if 0 <= col < len(row) and has_source_value(row[col])]
        if first:
            cover = _cover(row)
        kind = "cover" if first and cover else ""
        if not values and _is_source_header(row):
            kind = "source_header"
        elif not kind and cover and any(_cover_note_label(value) for value in row):
            kind = "note"
        yield number, values, kind
        first = False


def detect_header(rows: Iterable[Sequence], selected: Sequence[int], *, headerless: bool = False) -> int | None:
    return _select_header(header_candidates(rows, selected), headerless=headerless)


def read_only_xlsx_header(sheet, selected, override, *, include_headers: bool) -> tuple[int | None, Sequence]:
    if override:
        values = next(sheet.iter_rows(min_row=override, max_row=override, values_only=True), ())
        if not any(has_source_value(value) for value in values):
            raise _invalid_header(sheet.title, override)
        return override, values
    number = detect_header(sheet.iter_rows(min_row=1, values_only=True), selected, headerless=override == 0)
    if number and include_headers:
        return number, next(sheet.iter_rows(min_row=number, max_row=number, values_only=True), ())
    return number, ()


def _writable_xlsx_candidates(sheet, used_rows, selected: Sequence[int]) -> Iterator[tuple[int, list, str]]:
    if not used_rows:
        return
    first = used_rows[0]
    first_values = [cell.value for (row, _), cell in sheet._cells.items() if row == first and has_source_value(cell.value)]
    cover = _cover(first_values)
    notes = {row for (row, _), cell in sheet._cells.items() if _cover_note_label(cell.value)} if cover else set()
    other_headers = None
    for row in used_rows:
        values = [getattr(sheet._cells.get((row, col + 1)), "value", None) for col in selected]
        values = [value for value in values if has_source_value(value)]
        kind = "note" if row in notes else ""
        if row == first and cover:
            kind = "cover"
        if not values:
            if row == first:
                if _is_source_header(first_values):
                    kind = "source_header"
            else:
                if other_headers is None:
                    other_headers = _xlsx_source_header_rows(sheet)
                if row in other_headers:
                    kind = "source_header"
        yield row, values, kind


def _xlsx_source_header_rows(sheet) -> set[int]:
    fields: dict[int, set[str]] = {}
    for (row, _), cell in sheet._cells.items():
        field = _source_header_field(cell.value)
        if field:
            fields.setdefault(row, set()).add(field)
    rows = {row for row, labels in fields.items() if len(labels) >= 2}
    for (row, _), cell in sheet._cells.items():
        if row in rows and not _source_header_field(cell.value) and _is_data_or_title(cell.value):
            rows.remove(row)
    return rows


def writable_xlsx_header(sheet, used_rows, selected: Sequence[int], override: int | None) -> int | None:
    if override is not None:
        if override and override not in used_rows:
            raise _invalid_header(sheet.title, override)
        return override if used_rows else None
    return _select_header(_writable_xlsx_candidates(sheet, used_rows, selected))


def xls_manual_headers(book, selected: list[int], overrides: dict[int, int] | None) -> dict[str, int | None]:
    validate_header_rows(overrides, book.nsheets, selected)
    headers = {}
    if selected:
        for number, sheet in enumerate(book.sheets(), start=1):
            override = (overrides or {}).get(number)
            if override:
                if override > sheet.nrows or not any(has_source_value(v) for v in sheet.row_values(override - 1)):
                    raise _invalid_header(sheet.name, override)
                detected = override
            else:
                rows = (sheet.row_values(index) for index in range(sheet.nrows))
                detected = detect_header(rows, selected, headerless=override == 0)
            if detected is not None:
                headers[sheet.name] = detected - 1 if detected else None
    return headers
