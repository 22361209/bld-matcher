from __future__ import annotations

import re

from openpyxl.formula import Tokenizer
from openpyxl.formula.tokenizer import TokenizerError
from openpyxl.utils import get_column_letter
from xlutils.copy import copy as copy_xls
from xlutils.filter import BaseFilter, XLRDReader, XLWTWriter, process


def source_column_titles(used, selected, *, base):
    for index in sorted(used):
        letter = get_column_letter(index + 1 - base)
        if index in selected:
            yield index, "OE号" if len(selected) == 1 else f"OE号（{letter} 列）"
        else:
            yield index, f"{letter} 列"


class _PrependXlsHeader(BaseFilter):
    def __init__(self, shifted_sheets):
        self.shifted_sheets = shifted_sheets

    def sheet(self, rdsheet, wtsheet_name):
        self.offset = int(wtsheet_name in self.shifted_sheets)
        if self.offset and rdsheet.nrows >= 65536:
            raise ValueError("询价表已达到 XLS 行数上限，无法插入表头。")
        super().sheet(rdsheet, wtsheet_name)

    def row(self, rdrowx, wtrowx):
        self.next.row(rdrowx, wtrowx + self.offset)

    def cell(self, rdrowx, rdcolx, wtrowx, wtcolx):
        self.next.cell(rdrowx, rdcolx, wtrowx + self.offset, wtcolx)


def copy_xls_for_export(book, header_rows):
    shifted_sheets = {name for name, row in header_rows.items() if row is None}
    if not shifted_sheets:
        return copy_xls(book)
    writer = XLWTWriter()
    process(XLRDReader(book, "inquiry.xls"), _PrependXlsHeader(shifted_sheets), writer)
    return writer.output[0][1]


_ROW_REFERENCE = re.compile(r"(\$?[A-Za-z]{1,3}\$?|\$?)([1-9][0-9]*)")
_MAX_XLSX_ROW = 1048576


def _shift_reference(reference, sheet_name, shifted):
    # Sheet names can themselves contain '!'. Quoted/external references
    # stay intact and only the referenced workbook sheet determines the shift.
    prefix, separator, address = reference.rpartition("!")
    target = prefix.strip("'").replace("''", "'") if separator else sheet_name
    if target.casefold() not in shifted:
        return reference
    parts = []
    for part in address.split(":"):
        match = _ROW_REFERENCE.fullmatch(part)
        if match is not None:
            part = match[1] + str(min(int(match[2]) + 1, _MAX_XLSX_ROW))
        parts.append(part)
    return prefix + separator + ":".join(parts)


def _shift_formula(value, sheet_name, shifted):
    if not value:
        return value
    equal = value.startswith("=")
    try:
        tokens = Tokenizer(value if equal else "=" + value)
    except TokenizerError:
        # Preserve Excel formula extensions which openpyxl cannot tokenize.
        return value
    changed = False
    for token in tokens.items:
        if token.type == "OPERAND" and token.subtype == "RANGE":
            updated = _shift_reference(token.value, sheet_name, shifted)
            if updated != token.value:
                token.value = updated
                changed = True
    if not changed:
        return value
    result = tokens.render()
    return result if equal else result.removeprefix("=")


def _shift_ranges(ranges):
    items = list(ranges.ranges)
    ranges.ranges.clear()
    for area in items:
        # Whole-column rules already apply to all possible rows.
        if area.min_row != 1 or area.max_row != _MAX_XLSX_ROW:
            area.min_row = min(area.min_row + 1, _MAX_XLSX_ROW)
            area.max_row = min(area.max_row + 1, _MAX_XLSX_ROW)
        ranges.add(area)


def prepend_xlsx_header(sheet):
    if sheet.max_row >= _MAX_XLSX_ROW or max(sheet.row_dimensions, default=0) >= _MAX_XLSX_ROW:
        raise ValueError("询价表已达到 XLSX 行数上限，无法插入表头。")
    # insert_rows materialises a full rectangle out to distant styled cells.
    # Move only the actual cells, retaining their existing values and style objects.
    cells = sheet._cells
    sheet._cells = {}
    for (row, col), cell in cells.items():
        cell.row = row + 1
        sheet._cells[(row + 1, col)] = cell
        if cell.hyperlink:
            cell.hyperlink.ref = cell.coordinate
    sheet._current_row = max((r for r, _ in sheet._cells), default=1)
    for row in sorted(sheet.row_dimensions, reverse=True):
        dimension = sheet.row_dimensions.pop(row)
        dimension.index = row + 1
        sheet.row_dimensions[row + 1] = dimension
    _shift_ranges(sheet.merged_cells)
    shifted = {sheet.title.casefold()}
    if sheet.auto_filter.ref:
        sheet.auto_filter.ref = _shift_reference(sheet.auto_filter.ref, sheet.title, shifted)
    for table in sheet.tables.values():
        table.ref = _shift_reference(table.ref, sheet.title, shifted)
        if table.autoFilter and table.autoFilter.ref:
            table.autoFilter.ref = _shift_reference(table.autoFilter.ref, sheet.title, shifted)
    for validation in sheet.data_validations.dataValidation:
        _shift_ranges(validation.sqref)
    formats = sheet.conditional_formatting._cf_rules
    rules = list(formats.items())
    formats.clear()
    for setting, items in rules:
        _shift_ranges(setting.sqref)
        formats[setting] = items
    for drawing in [*sheet._images, *sheet._charts]:
        for marker in ("_from", "to"):
            point = getattr(drawing.anchor, marker, None)
            if point is not None:
                point.row += 1


def update_xlsx_header_references(workbook, shifted):
    if not shifted:
        return
    for sheet in workbook.worksheets:
        def formula(value):
            return _shift_formula(value, sheet.title, shifted)

        for cell in sheet._cells.values():
            if cell.data_type != "f":
                continue
            if isinstance(cell.value, str):
                cell.value = formula(cell.value)
            else:
                expr = cell.value
                expr.ref = _shift_reference(expr.ref, sheet.title, shifted)
                if getattr(expr, "text", None):
                    expr.text = formula(expr.text)
        for name in sheet.defined_names.values():
            name.attr_text = formula(name.attr_text)
        for validation in sheet.data_validations.dataValidation:
            validation.formula1 = formula(validation.formula1)
            validation.formula2 = formula(validation.formula2)
        for items in sheet.conditional_formatting._cf_rules.values():
            for rule in items:
                rule.formula = [formula(value) for value in rule.formula]
    for name in workbook.defined_names.values():
        name.attr_text = _shift_formula(name.attr_text, "", shifted)
