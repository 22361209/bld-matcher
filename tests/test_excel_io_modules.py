from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import xlrd
import xlwt

from app import excel_io
from app.matcher import ProductCatalog
from app.modules.inquiry.excel import export


class ExcelIoModuleTest(unittest.TestCase):
    def test_compatibility_facade_keeps_public_entrypoint(self) -> None:
        self.assertIs(excel_io.generate_excel_with_bld, export.generate_excel_with_bld)

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
