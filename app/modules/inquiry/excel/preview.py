from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from functools import partial
from pathlib import Path
from typing import TypeAlias

from openpyxl import load_workbook
import xlrd

from ..domain import InquiryValidationError
from .reader import _column_label, has_source_value
from .header_rows import detect_header


_ReadRows: TypeAlias = Callable[[int, int | None], Iterable[Sequence]]
PREVIEW_WINDOW_ROWS = 100


def _window_start(header: int, limit: int) -> int:
    return max(1, header - 2) if header > limit else 1


def _sheet_preview(name: str, rows: Iterable[Sequence], limit: int, header_row: int) -> dict:
    # Keep the requested window and enough populated rows to backfill a short
    # window at the end. Formatting alone never extends the displayed range.
    first = _window_start(header_row, limit)
    indexes: set[int] = set()
    window = {}
    tail = deque(maxlen=limit)
    last_row = 0
    for number, values in enumerate(rows, start=1):
        cells = {index: value for index, value in enumerate(values) if has_source_value(value)}
        if not cells:
            continue
        indexes.update(cells)
        last_row = number
        tail.append((number, cells))
        if first <= number < first + limit:
            window[number] = cells
    first = max(1, min(first, last_row - limit + 1))
    window.update(tail)
    columns = sorted(indexes)
    return {
        "sheet": name,
        "columns": [{"index": i, "label": _column_label(i)} for i in columns],
        "rows": [[window.get(row, {}).get(i) for i in columns] for row in range(first, min(first + limit, last_row + 1))],
        "row_start": first,
        "last_row": last_row,
    }


def _workbook_preview(
    sheets: Sequence[tuple[str, _ReadRows]], limit: int, header_rows: dict[int, int], selected: Sequence[int] | None,
) -> dict:
    pages = [
        _sheet_preview(name, read_rows(1, None), limit, header_rows.get(number, 0))
        for number, (name, read_rows) in enumerate(sheets, start=1)
    ]
    all_columns = {col["index"]: col for page in pages for col in page["columns"]}
    columns = [all_columns[i] for i in sorted(all_columns)]
    if selected is None:
        selected = [columns[0]["index"]] if columns else []
    for number, ((_, read_rows), page) in enumerate(zip(sheets, pages), start=1):
        indexes = [column["index"] for column in page["columns"]]
        detected = detect_header(read_rows(1, None), selected) if set(indexes).intersection(selected) else None
        page["detected_header"] = detected
        if number in header_rows or not detected:
            continue
        first = max(1, min(_window_start(detected, limit), page["last_row"] - limit + 1))
        if first != page["row_start"]:
            end = min(first + limit - 1, page["last_row"])
            page["rows"] = [[row[i] if i < len(row) else None for i in indexes] for row in read_rows(first, end)]
            page["row_start"] = first
    return {**pages[0], "sheets": pages, "all_columns": columns}


def _xls_rows(sheet, start: int, end: int | None):
    return (sheet.row_values(i) for i in range(start - 1, min(end or sheet.nrows, sheet.nrows)))


def _xlsx_rows(sheet, start: int, end: int | None):
    return sheet.iter_rows(min_row=start, max_row=end, values_only=True)


def _range_payload(rows: Iterable[Sequence], sheet_number: int, start: int) -> dict:
    items = [
        {"number": number, "cells": {i: str(value) for i, value in enumerate(row) if has_source_value(value)}}
        for number, row in enumerate(rows, start=start)
    ]
    return {"sheet_number": sheet_number, "row_start": start, "row_end": start + len(items) - 1, "rows": items}


def preview_inquiry_rows(path: Path, sheet_number: int, start: int, end: int | None) -> dict:
    suffix = path.suffix.lower()
    limits = {".xls": 65536, ".xlsx": 1048576}
    if suffix not in limits:
        raise InquiryValidationError("inquiry.file_type", "预览文件须为 XLS 或 XLSX。")
    if end is None:
        end = min(start + PREVIEW_WINDOW_ROWS - 1, limits[suffix])
    if not 1 <= start <= end <= limits[suffix] or end - start >= PREVIEW_WINDOW_ROWS:
        raise InquiryValidationError("inquiry.preview_range", f"请指定有效的源表行号，每次最多预览 {PREVIEW_WINDOW_ROWS} 行。")
    if suffix == ".xls":
        book = xlrd.open_workbook(
            str(path), on_demand=True, ragged_rows=True, formatting_info=True, ignore_workbook_corruption=True,
        )
        try:
            if not 1 <= sheet_number <= book.nsheets:
                raise InquiryValidationError("inquiry.preview_sheet", "所选工作表不存在。")
            return _range_payload(_xls_rows(book.sheet_by_index(sheet_number - 1), start, end), sheet_number, start)
        finally:
            book.release_resources()
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = workbook.worksheets
        if not 1 <= sheet_number <= len(sheets):
            raise InquiryValidationError("inquiry.preview_sheet", "所选工作表不存在。")
        sheet = sheets[sheet_number - 1]
        sheet.reset_dimensions()
        return _range_payload(_xlsx_rows(sheet, start, end), sheet_number, start)
    finally:
        workbook.close()


def preview_inquiry_columns(
    inquiry_path: Path,
    max_rows: int = 8,
    max_cols: int | None = 12,
    header_rows: dict[int, int] | None = None,
    match_columns: Sequence[int] | None = None,
) -> dict:
    if inquiry_path.suffix.lower() == ".xls":
        book = xlrd.open_workbook(
            str(inquiry_path),
            formatting_info=True,
            ignore_workbook_corruption=True,
            ragged_rows=max_cols is None,
        )
        sheet = book.sheet_by_index(0)
        if max_cols is None:
            sheets = [(item.name, partial(_xls_rows, item)) for item in book.sheets()]
            return _workbook_preview(sheets, max_rows, header_rows or {}, match_columns)
        cols = min(sheet.ncols, max_cols)
        rows = []
        for row_index in range(min(sheet.nrows, max_rows)):
            rows.append([sheet.cell_value(row_index, col_index) for col_index in range(cols)])
        return {
            "sheet": sheet.name,
            "columns": [{"index": i, "label": _column_label(i)} for i in range(cols)],
            "rows": rows,
        }

    workbook = load_workbook(inquiry_path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        if max_cols is None:
            # Read actual row widths so trailing formatting never pads every row to XFD.
            for item in workbook.worksheets:
                item.reset_dimensions()
            sheets = [(item.title, partial(_xlsx_rows, item)) for item in workbook.worksheets]
            return _workbook_preview(sheets, max_rows, header_rows or {}, match_columns)
        rows = []
        for values in sheet.iter_rows(min_row=1, max_row=max_rows, values_only=True):
            rows.append(list(values[:max_cols]))
        cols = min(max((len(row) for row in rows), default=0), max_cols)
        rows = [row + [None] * (cols - len(row)) for row in rows]
        return {
            "sheet": sheet.title,
            "columns": [{"index": i, "label": _column_label(i)} for i in range(cols)],
            "rows": rows,
        }
    finally:
        workbook.close()
