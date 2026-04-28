from __future__ import annotations

import sqlite3
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


BLD_HEADERS = ["BLD NO.", "品牌", "产品名称", "OE Reference", "Other Reference", "车型", "Product Image", "Unit Price", "状态", "更新时间"]
BRAND_HEADERS = ["NO.", "SERIES", "BLD NO.", "ITEM", "OE Reference", "Other Reference", "Models", "Product Image", "Unit Price", "状态", "更新时间"]
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def _font_for(value, *, bold: bool = False) -> Font:
    text = "" if value is None else str(value)
    return Font(name="微软雅黑" if CHINESE_RE.search(text) else "Arial", size=10, bold=bold)


def _setup_sheet(sheet, headers: list[str]) -> None:
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in sheet[1]:
        cell.font = _font_for(cell.value, bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _finish_sheet(sheet, widths: list[int]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    for row in sheet.iter_rows(min_row=2):
        max_lines = 1
        for cell in row:
            cell.font = _font_for(cell.value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if isinstance(cell.value, str):
                max_lines = max(max_lines, cell.value.count("\n") + 1)
        sheet.row_dimensions[row[0].row].height = min(180, max(24, max_lines * 15))

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _image_reference(row) -> str:
    if row["image_path"]:
        return row["image_path"]
    return f"product_images/{row['bld_no']}.jpg"


def export_products_xlsx(
    conn: sqlite3.Connection,
    output_path: Path,
    include_inactive: bool = True,
    export_format: str = "bld",
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "产品目录"

    sql = "SELECT * FROM products"
    if not include_inactive:
        sql += " WHERE active = 1"
    if export_format == "brand":
        sql += " ORDER BY series, bld_no"
        _setup_sheet(sheet, BRAND_HEADERS)
    else:
        sql += " ORDER BY bld_no"
        _setup_sheet(sheet, BLD_HEADERS)

    for number, row in enumerate(conn.execute(sql), start=1):
        if export_format == "brand":
            sheet.append(
                [
                    number,
                    row["series"],
                    row["bld_no"],
                    row["item"],
                    row["oe_no_1"],
                    row["oe_no_2"],
                    row["models"],
                    _image_reference(row),
                    row["price_cny"],
                    "启用" if row["active"] else "停用",
                    row["updated_at"],
                ]
            )
        else:
            sheet.append(
                [
                    row["bld_no"],
                    row["series"],
                    row["item"],
                    row["oe_no_1"],
                    row["oe_no_2"],
                    row["models"],
                    _image_reference(row),
                    row["price_cny"],
                    "启用" if row["active"] else "停用",
                    row["updated_at"],
                ]
            )

    widths = [10, 18, 16, 28, 34, 28, 34, 22, 12, 10, 20] if export_format == "brand" else [14, 18, 28, 34, 28, 34, 22, 12, 10, 20]
    _finish_sheet(sheet, widths)
    price_col = 9 if export_format == "brand" else 8
    for cell in sheet.iter_cols(min_col=price_col, max_col=price_col, min_row=2):
        for item in cell:
            item.number_format = '¥#,##0.00'

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return output_path
