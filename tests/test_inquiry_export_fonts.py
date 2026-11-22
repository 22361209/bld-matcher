from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font
import xlrd
import xlwt

from app.excel_io import generate_excel_with_bld
from app.matcher import ProductCatalog
from app.modules.inquiry.excel.output_style import match_column_fonts, write_result_row


class InquiryExportFontsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.catalog = ProductCatalog([{
            "BLD NO.": "K-72110", "OE NO.1": "OE-72110", "price_cny": 125.37,
            "product_status": "带球头",
        }])

    def test_xlsx_result_fonts_follow_source_and_override_spare_column_formatting(self):
        source = Workbook()
        sheet = source.active
        sheet.title = "询价单"
        sheet.column_dimensions.group("A", "B", hidden=False)
        sheet.column_dimensions["A"].font = Font(name="Arial", size=17)
        sheet["B1"] = "OE号"
        sheet["B1"].font = Font(name="Arial", size=18, bold=True)
        sheet["B2"] = sheet["B3"] = sheet["B4"] = "OE-72110"
        sheet["B2"].font = Font(name="Arial", size=13)
        sheet.row_dimensions[3].font = Font(name="Arial", size=15)
        sheet.row_dimensions[3].height = 28
        for row in [3, 4]:
            sheet.cell(row, 2).number_format = "@"
        default_sheet = source.create_sheet("默认字体")
        default_sheet["B1"] = "OE号"
        default_sheet["B2"] = "OE-72110"
        default_font = source._fonts[0]
        for target in source:
            for idx, column in enumerate("CDEF"):
                target.column_dimensions[column].font = Font(size=6 + idx)
                cell = target[f"{column}2"]
                cell.font = Font(size=24 + idx)
                cell.alignment = Alignment(shrinkToFit=True)
                cell.number_format = "yyyy-mm-dd"
        source.save(self.root / "in.xlsx")
        source.close()

        generate_excel_with_bld(self.root / "in.xlsx", self.root / "out.xlsx", self.catalog,
                                match_column=1, price_mode="tax")
        book = load_workbook(self.root / "out.xlsx")
        self.addCleanup(book.close)
        sheet = book["询价单"]
        self.assertEqual([sheet.cell(1, n).value for n in range(3, 7)],
                         ["BLD NO.", "含税单价", "产品状态", "匹配说明"])
        for row, size in enumerate((18, 13, 15, 17), start=1):
            for column in "CDEF":
                cell = sheet[f"{column}{row}"]
                self.assertEqual((cell.font.name, cell.font.size, cell.font.bold), ("Arial", size, row == 1))
                self.assertFalse(cell.alignment.shrinkToFit)
                self.assertEqual(cell.number_format, "0.00" if column == "D" and row > 1 else "General")
        self.assertEqual(sheet["D2"].data_type, "n")
        self.assertEqual(sheet["D2"].value, 125.37)
        self.assertEqual(sheet["B2"].font.size, 13)
        self.assertEqual(sheet["B3"].number_format, "@")
        self.assertEqual(sheet.row_dimensions[3].height, 28)
        for column in "CDEF":
            cell = book["默认字体"][f"{column}2"]
            self.assertEqual(cell.font, default_font)
            self.assertTrue(cell.has_style)

    def test_xls_export_writes_one_consistent_font_to_all_result_columns(self):
        source = xlwt.Workbook()
        sheet = source.add_sheet("询价单")
        sheet.write(0, 0, "OE号")
        sheet.write(1, 0, "OE-72110")
        for col in range(1, 5):
            style = xlwt.easyxf(f"font: height {100 + 80 * col};", num_format_str="YYYY-MM-DD")
            sheet.write(1, col, None, style)
        source.save(self.root / "in.xls")

        generate_excel_with_bld(self.root / "in.xls", self.root / "out.xls", self.catalog, price_mode="tax")
        book = xlrd.open_workbook(self.root / "out.xls", formatting_info=True)
        result = book.sheet_by_index(0)
        self.assertEqual(result.cell_value(1, 1), "K-72110")
        self.assertEqual(result.cell_type(1, 2), xlrd.XL_CELL_NUMBER)
        self.assertEqual(result.cell_value(1, 2), 125.37)
        font_ids = {
            book.xf_list[result.cell_xf_index(row, col)].font_index for row in range(2) for col in range(1, 5)
        }
        self.assertEqual(len(font_ids), 1)

    def test_multiple_match_columns_reuse_sheet_font_lookup_across_rows(self):
        book = Workbook()
        sheet = book.active
        sheet.column_dimensions.group("A", "C", hidden=False)
        sheet.column_dimensions["A"].font = Font(size=18)
        for row in range(1, 21):
            sheet.cell(row, 2 if row % 2 else 5, "OE-72110")
        with patch.object(sheet.column_dimensions, "values", wraps=sheet.column_dimensions.values) as scan:
            fonts = match_column_fonts(sheet, [5, 2])
            for row in range(1, 21):
                write_result_row(sheet, row, 6, fonts, ["K-72110"])
            scan.assert_called_once()
        for row in range(1, 21):
            self.assertEqual(sheet.cell(row, 6).font.size, 18 if row % 2 else book._fonts[0].size)
        book.close()
