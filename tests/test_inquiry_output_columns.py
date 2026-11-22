from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook, load_workbook
import xlrd
import xlwt

from app.matcher import ProductCatalog
from app.modules.inquiry.excel.export import generate_excel_with_bld


class InquiryOutputColumnsTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.catalog = ProductCatalog([{"BLD NO.": "K78452L", "OE NO.1": "OE1234567", "price_cny": 12}])

    def test_xlsx_reuses_visible_gap_preserving_hidden_data_merges_and_styles(self):
        book = Workbook()
        sheet = book.active
        sheet["A1"], sheet["A2"] = "OE号", "OE1234567"
        sheet["B2"], sheet["C2"], sheet["D2"] = 0, False, "=1+1"
        sheet.merge_cells("F1:G1")
        sheet.merge_cells("AD1:AJ1")
        sheet["AD1"] = "远处合并说明栏"
        sheet.column_dimensions.group("E", "Q", hidden=True)
        sheet["N2"] = "隐藏原值"
        sheet["XFD2"].number_format = "0.00"
        book.save(self.root / "source.xlsx")
        generate_excel_with_bld(self.root / "source.xlsx", self.root / "result.xlsx", self.catalog, price_mode="tax")
        result = load_workbook(self.root / "result.xlsx")
        self.addCleanup(result.close)
        sheet = result.active
        self.assertEqual([cell.value for cell in sheet["H1:K1"][0]], ["BLD NO.", "含税单价", "产品状态", "匹配说明"])
        self.assertEqual([cell.value for cell in sheet["H2:I2"][0]], ["K78452L", 12])
        self.assertEqual(sheet["D2"].value, "=1+1")
        self.assertEqual(sheet["N2"].value, "隐藏原值")
        self.assertEqual(sheet["AD1"].value, "远处合并说明栏")
        self.assertIn("F1:G1", sheet.merged_cells)
        self.assertEqual(sheet["XFD2"].number_format, "0.00")
        self.assertEqual([(dim.min, dim.max, dim.hidden) for dim in sheet.column_dimensions.values()],
                         [(5, 7, True), (8, 11, False), (12, 17, True)])

    def test_xls_respects_uncached_formula_and_reuses_styled_hidden_blanks(self):
        book = xlwt.Workbook()
        sheet = book.add_sheet("询价")
        sheet.write(0, 0, "OE号")
        sheet.write(1, 0, "OE1234567")
        sheet.write(1, 1, False)
        sheet.write(1, 2, xlwt.Formula("1+1"))
        sheet.write(1, 7, 0)
        sheet.write_merge(0, 0, 26, 30, "远处合并说明栏")
        style = xlwt.easyxf(num_format_str="0.00")
        for col in range(3, 8):
            sheet.col(col).hidden = True
            sheet.write(0, col, None, style)
        sheet.write(1, 255, None, style)
        book.save(str(self.root / "source.xls"))
        generate_excel_with_bld(self.root / "source.xls", self.root / "result.xls", self.catalog, price_mode="tax")
        result = xlrd.open_workbook(self.root / "result.xls", formatting_info=True).sheet_by_index(0)
        self.assertEqual(result.row_values(0, 3, 7), ["BLD NO.", "含税单价", "产品状态", "匹配说明"])
        self.assertEqual(result.row_values(1, 3, 5), ["K78452L", 12])
        self.assertEqual(result.cell_value(1, 7), 0)
        self.assertEqual(result.cell_value(0, 26), "远处合并说明栏")
        self.assertTrue(result.colinfo_map[7].hidden)
        self.assertTrue(all(not result.colinfo_map[col].hidden for col in range(3, 7)))
