from __future__ import annotations

from copy import copy

from openpyxl.utils import get_column_letter
import xlrd

from .reader import has_source_value


def _output_start(last_data_rows, header_row, hidden, merges, count, first, last):
    data_columns = (col for col, row in last_data_rows.items() if row >= header_row and col not in hidden)
    start = max(data_columns, default=first - 1) + 1
    # Keep distant hidden data and merged notes in place if there is a gap before them.
    obstacles = [(column, column) for column in last_data_rows.keys() & hidden]
    for left, right in sorted([*obstacles, *merges]):
        if left >= start + count:
            break
        if right >= start:
            start = right + 1
    if start + count - 1 > last:
        raise ValueError("源表右侧列数不足，无法放下匹配结果。")
    return start


def xls_source_columns(source):
    last_data_rows = {}
    merged_anchors = {(top, left) for top, _, left, end in source.merged_cells if end > left + 1}
    # An uncached BIFF formula is reported as a text cell containing an empty string.
    for row in range(source.nrows):
        for col, kind in enumerate(source.row_types(row)):
            if kind in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                continue
            if kind == xlrd.XL_CELL_TEXT:
                text = source.cell_value(row, col)
                if text and not text.strip():
                    continue
            if (row, col) in merged_anchors:
                last_data_rows.setdefault(col, -1)
            else:
                last_data_rows[col] = row
    return last_data_rows


def prepare_xls_output_columns(source, target, last_data_rows, header_row, count):
    hidden = {col for col, info in source.colinfo_map.items() if info.hidden or info.width == 0}
    merges = [(left, end - 1) for _, bottom, left, end in source.merged_cells if bottom > header_row]
    start = _output_start(last_data_rows, header_row, hidden, merges, count, 0, 255)
    for index in range(start, start + count):
        col = target.col(index)
        col.hidden = col.collapsed = col.level = 0
        if not col.width:
            col.width = 256 * 13
    return start


def xlsx_source_cells(sheet):
    """Inspect instantiated cells only. iter_rows would create cells throughout the styled extent."""
    last_data_rows, used_rows = {}, set()
    merged_anchors = {(area.min_row, area.min_col) for area in sheet.merged_cells.ranges if area.min_col < area.max_col}
    header_rows = [[] for _ in range(20)]
    for (row, col), cell in sheet._cells.items():
        if not has_source_value(cell.value):
            continue
        last_row = last_data_rows.setdefault(col, 0)
        if (row, col) not in merged_anchors:
            last_data_rows[col] = max(last_row, row)
        used_rows.add(row)
        if row <= len(header_rows):
            values = header_rows[row - 1]
            if col > len(values):
                values.extend([None] * (col - len(values)))
            values[col - 1] = cell.value
    return last_data_rows, sorted(used_rows), header_rows


def prepare_xlsx_output_columns(sheet, last_data_rows, header_row, last_row, count):
    hidden = set()
    dimensions = list(sheet.column_dimensions.items())
    for _, dim in dimensions:
        dim.reindex()
        if dim.hidden or dim.width == 0:
            hidden.update(range(dim.min, dim.max + 1))
    merges = [(area.min_col, area.max_col) for area in sheet.merged_cells.ranges
              if area.max_row >= header_row and area.min_row <= last_row]
    start = _output_start(last_data_rows, header_row, hidden, merges, count, 1, 16384)
    end = start + count - 1
    for key, dim in dimensions:
        if dim.max < start or dim.min > end or not (dim.hidden or dim.outlineLevel or dim.collapsed or not dim.width):
            continue
        # Split the grouped column definition so neighbouring source columns keep their settings.
        del sheet.column_dimensions[key]
        segments = ((dim.min, start - 1), (max(start, dim.min), min(end, dim.max)), (end + 1, dim.max))
        for left, right in segments:
            if left > right:
                continue
            segment = copy(dim)
            segment.min, segment.max, segment.index = left, right, get_column_letter(left)
            if start <= left <= end:
                segment.hidden = segment.collapsed = False
                segment.outlineLevel = 0
                segment.width = segment.width or 13
            sheet.column_dimensions[segment.index] = segment
    return start
