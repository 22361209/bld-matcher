from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import xlrd
import xlwt
from openpyxl import Workbook

from app import excel_io
from app.matcher import ProductCatalog
from app.modules.inquiry.excel import export
from app.modules.inquiry.excel.header_rows import detect_header


class ExcelIoModuleTest(unittest.TestCase):
    def test_compatibility_facade_keeps_public_entrypoint(self) -> None:
        self.assertIs(excel_io.generate_excel_with_bld, export.generate_excel_with_bld)

    def test_header_detection_preserves_letter_only_part_numbers(self) -> None:
        cases = [
            (["编号", "API-FILE-OE"], 1),
            (["用户自定义型号", "ALPHA"], 1),
            (["用户自定义型号", "Api-File"], 1),
            (["API-FILE-OE", "OE号", "API-FILE-REF"], 0),
            (["PART-NO", "ALPHA"], 1),
            (["OE/OEM", "ALPHA"], 1),
            (["使用说明", "联系供应商", "备注：需核实"], None),
            ([], None),
        ]
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(detect_header(((value,) for value in values), [0]), expected)

    def test_web_preview_lists_all_populated_columns_by_original_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            modern = Workbook()
            sheet = modern.active
            sheet["B1"] = "OE号"
            sheet["AA1"] = " 远列 "
            sheet["AA2"] = False
            sheet.column_dimensions["AA"].hidden = True
            sheet["AC11"] = 0
            sheet["AD1"] = " \n\t "
            sheet["XFD1"].number_format = "0.00"
            modern.save(root / "source.xlsx")
            modern.close()

            legacy = xlwt.Workbook()
            sheet = legacy.add_sheet("询价")
            sheet.write(0, 1, "OE号")
            sheet.write(0, 26, " 远列 ")
            sheet.write(1, 26, False)
            sheet.col(26).hidden = True
            sheet.write(10, 28, 0)
            sheet.write(0, 29, " \n\t ")
            sheet.write(0, 255, None, xlwt.easyxf(num_format_str="0.00"))
            legacy.save(root / "source.xls")

            for path in (root / "source.xlsx", root / "source.xls"):
                with self.subTest(path=path):
                    preview = excel_io.preview_inquiry_columns(path, max_cols=None)
                    self.assertEqual(preview["columns"], [
                        {"index": 1, "label": "B"}, {"index": 26, "label": "AA"}, {"index": 28, "label": "AC"},
                    ])
                    self.assertEqual(len(preview["rows"]), 8)
                    self.assertEqual(preview["rows"][0][:2], ["OE号", " 远列 "])
                    self.assertIn(preview["rows"][0][2], (None, ""))
                    self.assertEqual(preview["rows"][1][1], False)
                    bounded = excel_io.preview_inquiry_columns(path, max_rows=5, max_cols=8)
                    self.assertEqual(len(bounded["columns"]), 8)
                    self.assertEqual(len(bounded["rows"]), 5)

    def test_legacy_xls_export_keeps_sheet_and_appended_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source_path = root / "legacy-inquiry.xls"
            output_path = root / "legacy-result.xls"
            workbook = xlwt.Workbook()
            sheet = workbook.add_sheet("询价")
            sheet.write(0, 0, "OE号")
            sheet.write(0, 1, "产品名称")
            sheet.write(1, 0, "LEGACY-OE-001")
            sheet.write(1, 1, "Legacy Arm")
            workbook.save(str(source_path))

            catalog = ProductCatalog(
                [
                    {
                        "BLD NO.": "K-LEGACY-001",
                        "OE NO.1": "LEGACY-OE-001",
                        "OE NO.2": "",
                        "SERIES": "TEST",
                        "ITEM": "Legacy Arm",
                        "Models": "Legacy Model",
                        "price_cny": 110,
                        "product_status": "启用",
                    }
                ]
            )
            summary = excel_io.generate_excel_with_bld(
                source_path,
                output_path,
                catalog,
                price_mode="net",
            )

            self.assertEqual(summary["matched"], 1)
            exported = xlrd.open_workbook(output_path)
            result_sheet = exported.sheet_by_name("询价")
            self.assertEqual(result_sheet.cell_value(0, 2), "BLD NO.")
            self.assertEqual(result_sheet.cell_value(0, 3), "不含税单价")
            self.assertEqual(result_sheet.cell_value(1, 2), "K-LEGACY-001")
            self.assertEqual(result_sheet.cell_value(1, 3), 100)


if __name__ == "__main__":
    unittest.main()
