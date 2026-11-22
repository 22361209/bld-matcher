from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence

from openpyxl.styles import Alignment, Font
from openpyxl.utils import column_index_from_string

from .reader import has_source_value


# An explicit alignment also forces an XF record for workbook-default fonts.
# Otherwise Excel can display a formatted destination column's font instead.
_OUTPUT_ALIGNMENT = Alignment(vertical="bottom", shrinkToFit=False)


def match_column_fonts(sheet, match_columns: Sequence[int]) -> dict[int, Font]:
    default_font: Font = sheet.parent._fonts[sheet.parent._cell_styles[0].fontId]
    fonts = {column: default_font for column in match_columns}
    columns = sorted(fonts)
    for dimension in sheet.column_dimensions.values():
        style = dimension._style
        if style is None or not style.fontId:
            continue
        start = dimension.min or column_index_from_string(dimension.index)
        end = dimension.max or start
        for column in columns[bisect_left(columns, start):bisect_right(columns, end)]:
            fonts[column] = sheet.parent._fonts[style.fontId]
    return fonts


def _source_font(sheet, row: int, column_fonts: dict[int, Font]) -> Font:
    column = next(
        (index for index in column_fonts if has_source_value(getattr(sheet._cells.get((row, index)), "value", None))),
        next(iter(column_fonts)),
    )
    for source in (sheet._cells.get((row, column)), sheet.row_dimensions.get(row)):
        style = getattr(source, "_style", None)
        # The public font accessor also returns font 0 for cells styled only
        # with a number format. Such a cell still needs its row or column font.
        if style is not None and style.fontId:
            return sheet.parent._fonts[style.fontId]
    return column_fonts[column]


def write_result_row(
    sheet,
    row: int,
    first_column: int,
    column_fonts: dict[int, Font],
    values: Sequence[object],
    *,
    price_format: str | None = None,
) -> None:
    font = _source_font(sheet, row, column_fonts)
    for offset, value in enumerate(values):
        cell = sheet.cell(row, first_column + offset)
        # Loaded cells can share a style array with original cells. Start fresh
        # so overwriting a formatted blank cannot change the source formatting.
        cell._style = None
        cell.value = value
        cell.font = font
        cell.alignment = _OUTPUT_ALIGNMENT
        if offset == 1 and price_format:
            cell.number_format = price_format
