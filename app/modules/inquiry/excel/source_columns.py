from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime, time
from typing import Literal

import xlrd

from .reader import _column_label, has_source_value


def _source_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        if value.time() == time.min:
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def xls_source_values(sheet, row_index: int, datemode: Literal[0, 1]) -> Iterator[object]:
    for cell in sheet.row(row_index):
        value = cell.value
        if cell.ctype == xlrd.XL_CELL_BOOLEAN:
            value = bool(value)
        elif cell.ctype == xlrd.XL_CELL_DATE:
            try:
                date_value = xlrd.xldate_as_datetime(value, datemode)
                value = date_value.time() if 0 <= value < 1 else date_value
            except (OverflowError, ValueError):
                # Out-of-range date-formatted numbers still have a readable value.
                pass
        elif cell.ctype == xlrd.XL_CELL_ERROR:
            value = xlrd.error_text_from_code.get(value, value)
        yield value


class SourceSheetColumns:
    """Collect populated columns for one sheet alongside the normal match scan."""

    def __init__(
        self, sheet_number: int, name: str, headers: Iterable[object], *, unnamed_match_column: int | None = None
    ) -> None:
        self.sheet_number = sheet_number
        self.name = name
        self.unnamed_match_column = unnamed_match_column
        self.columns: dict[int, dict[str, str]] = {}
        self.has_rows = False
        for index, value in enumerate(headers):
            header = _source_text(value).strip()
            if header:
                self._add_column(index, header)

    def _add_column(self, index: int, header: str = "") -> dict[str, str]:
        letter = _column_label(index)
        if index == self.unnamed_match_column:
            header = "OE号"
        column = {
            "key": f"source-{self.sheet_number}-{letter}",
            "label": f"{letter} · {header}" if header else letter,
        }
        self.columns[index] = column
        return column

    def attach_row(self, row: dict, values: Iterable[object]) -> None:
        self.has_rows = True
        cells = {}
        for index, value in enumerate(values):
            if not has_source_value(value):
                continue
            column = self.columns.get(index) or self._add_column(index)
            cells[column["key"]] = _source_text(value)
        row["source_sheet"] = self.name
        row["source_values"] = cells

    def append_to(self, sheets: list[dict]) -> None:
        if self.has_rows:
            sheets.append({"name": self.name, "columns": [self.columns[i] for i in sorted(self.columns)]})
