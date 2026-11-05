from __future__ import annotations

from datetime import date, time
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl import Workbook

from app.modules.costing.import_values import FormulaUnavailable, WorkbookValues
from app.modules.costing.workbook_import import WorkbookImportError, parse_cost_workbook


_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def write_fixture(path: Path, workbook: Workbook, cache: dict[str, dict[str, object]] | None = None) -> None:
    """Place explicit formula caches in synthetic OOXML, without using Excel."""
    workbook.save(path)
    if not cache:
        return
    with ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    for sheet, cells in cache.items():
        part = f"xl/worksheets/sheet{workbook.sheetnames.index(sheet) + 1}.xml"
        root = ET.fromstring(files[part])
        for cell in root.iter(f"{_NS}c"):
            if cell.attrib["r"] not in cells:
                continue
            cached = cells[cell.attrib["r"]]
            if isinstance(cached, str):
                cell.set("t", "str")
            value = cell.find(f"{_NS}v")
            if value is None:
                value = ET.SubElement(cell, f"{_NS}v")
            value.text = str(cached)
        files[part] = ET.tostring(root, encoding="utf-8")
    with ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def arm_book() -> Workbook:
    book = Workbook()
    sheet = book.active
    sheet.title = "摆臂成本核算"
    for row, name, price in ((1, "冲压制造费", 0.625), (2, "安装制造费", 0.54), (3, "打包带", 0.056), (6, "运费", 0.6)):
        sheet[f"D{row}"], sheet[f"H{row}"] = name, price
    headers = ["本厂编码", "车系", "适用车型", "部件类别", "部件名称", "外协单位", "零件编码", "金额", "单价", "数量", "重量", "规格", "规格", "规格", "加工费", "备注"]
    for column, header in enumerate(headers, 1):
        sheet.cell(7, column, header)
    return book


def add_arm(book: Workbook, row: int, name: str | None, amount: object, code: str = "K1001B", **cells) -> None:
    sheet = book["摆臂成本核算"]
    for column, value in {"A": code, "B": "测试品牌", "C": "测试车型", "D": "焊接组件", "E": name, "H": amount, **cells}.items():
        sheet[f"{column}{row}"] = value


def add_pivot(book: Workbook, code: str, detail: object, approved: object, credit=None, theory=None, series=None) -> None:
    sheet = book.create_sheet("透视表")
    for column, value in {"A": code, "B": detail, "C": approved, "F": credit, "K": theory, "J": series}.items():
        sheet[f"{column}4"] = value


class CostingImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "成本测试.xlsx"

    def parse(self, book: Workbook, cache=None) -> dict:
        write_fixture(self.path, book, cache)
        return parse_cost_workbook(self.path)

    def test_mixed_costs_preserve_approved_value_and_quantity_meaning(self):
        book = arm_book()
        add_arm(book, 8, "固定工费", 1)
        add_arm(book, 9, "钢板", "=I9*K9", F="钢板供应商", I=5, J=10, K=0.2)
        add_arm(book, 10, "螺栓", "=I10*J10", I=0.25, J=3)
        add_arm(book, 11, "管件", "=(I11*K11+O11)*J11", I=0.5, J=3, K=0.4, O=0.1)
        add_arm(book, 12, "冲压工费", "=0.2+H1")
        add_pivot(book, "K1001B", 4.475, 5, credit=0.3)
        result = self.parse(book, {"摆臂成本核算": {"H9": 1, "H10": 0.75, "H11": 0.9, "H12": 0.825}})
        record = result["records"][0]
        self.assertEqual([item["mode"] for item in record["items"]], ["fixed", "weight", "unit", "weight", "fixed"])
        self.assertEqual(record["items"][1]["quantity"], "1")
        self.assertEqual(record["items"][1]["supplier"], "钢板供应商")
        self.assertEqual(record["items"][1]["source_info"]["raw"]["J"], "10")
        self.assertEqual(record["approved_cost"], "5")
        self.assertEqual(record["scrap_credit"], "0.3")
        self.assertEqual(record["source_info"]["imported_known_subtotal"], "4.475")
        self.assertEqual(result["summary"]["formula_differences"], 0)
        self.assertEqual(record["items"][-1]["source_info"]["reference_values"], {"H1": "0.625"})

    def test_blank_amount_is_missing_and_blank_slot_is_not_an_item(self):
        book = arm_book()
        add_arm(book, 8, "缺单价配件", None)
        add_arm(book, 9, None, None)
        add_arm(book, 10, "明确零成本", 0)
        result = self.parse(book)
        self.assertEqual(result["summary"]["items"], 2)
        self.assertEqual(result["summary"]["skipped_empty_slots"], 1)
        self.assertIsNone(result["records"][0]["items"][0]["amount"])
        self.assertEqual(result["records"][0]["items"][1]["amount"], "0")

    def test_subtotal_is_not_counted_twice_and_generic_sum_is_not_dropped(self):
        book = arm_book()
        add_arm(book, 8, "配件一", 2)
        add_arm(book, 9, "配件二", 3)
        add_arm(book, 10, None, "=SUBTOTAL(9,H8:H9)")
        add_arm(book, 11, "材料加加工", "=SUM(I11:O11)", I=1, O=0.2)
        result = self.parse(book, {"摆臂成本核算": {"H10": 5, "H11": 1.2}})
        self.assertEqual(result["summary"]["skipped_subtotals"], 1)
        self.assertEqual(result["summary"]["items"], 3)
        self.assertEqual(result["records"][0]["source_info"]["imported_known_subtotal"], "6.2")
        self.assertEqual(result["records"][0]["source_info"]["excluded_rows"][0]["row"], 10)

    def test_cache_and_pivot_differences_are_reviewed(self):
        book = arm_book()
        add_arm(book, 8, "材料", "=I8*K8", I=10, K=0.2)
        add_pivot(book, "K1001B", 20, 21)
        result = self.parse(book, {"摆臂成本核算": {"H8": 20}})
        item = result["records"][0]["items"][0]
        self.assertEqual(item["source_info"]["recomputed_amount"], "2.0")
        self.assertEqual(item["source_info"]["cached"]["H"], "20")
        self.assertEqual(result["summary"]["formula_differences"], 1)
        self.assertEqual(result["summary"]["pivot_differences"], 1)
        self.assertIn("缓存金额", item["review_note"])
        self.assertEqual(result["records"][0]["approved_cost"], "21")

    def _carton_book(self, duplicate=False):
        book = arm_book()
        add_arm(book, 8, "40*20*10", "=(I8+(((L8+N8)*4+(M8+N8)*4+(L8+M8)*2)*$H$3)/100)/J8",
                D="外箱", G="原表-G8", J=10)
        sheet = book["摆臂成本核算"]
        for target, source in (("I", "C"), ("L", "D"), ("M", "E"), ("N", "F")):
            sheet[f"{target}8"] = f"=_xlfn.XLOOKUP(E8,纸箱!B:B,纸箱!{source}:{source})"
        cartons = book.create_sheet("纸箱")
        cartons.append(["编号", "规格", "单价", "长", "宽", "高"])
        cartons.append(["ZX1", "40*20*10", 5, 40, 20, 10])
        if duplicate:
            cartons.append(["ZX2", "40*20*10", 7, 40, 20, 10])
        return book

    def test_package_resolves_current_lookup_and_has_clear_inputs(self):
        result = self.parse(self._carton_book())
        item = result["records"][0]["items"][0]
        self.assertEqual(item["mode"], "package")
        self.assertEqual((item["name"], item["category"], item["product_code"], item["part_code"]),
                         ("纸箱", "外包装", "40*20*10", "原表-G8"))
        self.assertEqual(item["source_info"]["raw"]["D"], "外箱")
        self.assertEqual(item["source_info"]["raw"]["E"], "40*20*10")
        self.assertIn("XLOOKUP(E8,纸箱!", item["source_info"]["raw"]["I"])
        self.assertEqual(item["quantity"], "10")
        self.assertEqual(item["unit_price"], "5")
        self.assertEqual(item["source_info"]["recomputed_amount"], "0.52464")
        self.assertEqual(result["references"]["cartons"][0]["code"], "ZX1")

    def test_duplicate_lookup_keeps_cached_value_and_requires_review(self):
        result = self.parse(self._carton_book(duplicate=True), {"摆臂成本核算": {"H8": 0.52464}})
        item = result["records"][0]["items"][0]
        self.assertEqual(item["mode"], "fixed")
        self.assertEqual(item["amount"], "0.52464")
        self.assertIn("规格重复", item["review_note"])

    def test_unsupported_cross_row_formula_is_snapshot_not_live_link(self):
        book = arm_book()
        add_arm(book, 8, "特殊工费", "=H9+0.1")
        add_arm(book, 9, "另一行", 2)
        result = self.parse(book, {"摆臂成本核算": {"H8": 2.1}})
        item = result["records"][0]["items"][0]
        self.assertEqual(item["amount"], "2.1")
        self.assertIn("跨行引用", item["review_note"])
        self.assertEqual(item["source_info"]["original_formula"], "=H9+0.1")

    def test_negative_adjustment_and_dates_are_preserved(self):
        book = arm_book()
        add_arm(book, 8, "返还", "=(I8*K8+O8)*-1", I=9, K=0.16, O=0.3, P=date(2026, 11, 5), Q=time(8, 30))
        result = self.parse(book, {"摆臂成本核算": {"H8": -1.74}})
        item = result["records"][0]["items"][0]
        self.assertEqual((item["mode"], item["amount"]), ("adjustment", "-1.74"))
        self.assertEqual(item["source_info"]["raw"]["Q"], "08:30:00")
        json.dumps(result, allow_nan=False)

    def test_main_and_secondary_scrap_rows_are_both_preserved(self):
        book = arm_book()
        add_arm(book, 8, "材料", 10, code="K8003LA")
        add_pivot(book, "K8003LA", 10, 10, credit=2, theory=2.74, series=8003)
        scrap = book.create_sheet("片件重量")
        scrap.append(["产品编码", "产品名称", "单价", "毛重", "净重", "废料重量", "废料比例", "废料金额"])
        scrap.append([8003, "主板", 2, 2, 1.1, 0.9, None, 1.8])
        scrap.append([8003, "副板", 2, 1, 0.53, 0.47, None, 0.94])
        result = self.parse(book)
        source = result["records"][0]["source_info"]
        self.assertEqual(len(source["scrap_reference_rows"]), 2)
        self.assertEqual(source["scrap_known_total"], "2.74")
        self.assertEqual(result["records"][0]["scrap_credit"], "2")
        self.assertEqual(result["records"][0]["theoretical_scrap"], "2.74")

    def test_reject_wrong_headers_without_guessing_columns(self):
        book = arm_book()
        book["摆臂成本核算"]["H7"] = "其他金额"
        add_arm(book, 8, "材料", 1)
        with self.assertRaisesRegex(WorkbookImportError, "未猜测列位置"):
            self.parse(book)

    def test_arithmetic_rejects_functions_exponentiation_and_cycles(self):
        values = WorkbookValues({"test": [("=A2",), ("=A1",)]}, {})
        for expression in ("__import__('os')", "2**100000", "1/0", "A1"):
            with self.subTest(expression=expression), self.assertRaises(FormulaUnavailable):
                values.arithmetic("test", expression)

    def test_repeating_decimal_snapshot_remains_acceptable_domain_input(self):
        from app.modules.costing.domain import calculate_cost

        book = arm_book()
        add_arm(book, 8, "分摊费", "=1/3")
        result = self.parse(book)
        self.assertEqual(result["records"][0]["items"][0]["amount"], "0.333333333333333333")
        self.assertEqual(result["records"][0]["items"][0]["source_info"]["arithmetic_result"], "0." + "3" * 28)
        self.assertEqual(calculate_cost(result["records"][0])["status"], "ready")

    def test_extreme_exponents_in_cells_caches_references_and_calculations_are_bounded(self):
        book = arm_book()
        add_arm(book, 8, "直接金额", "1e-200000")
        add_arm(book, 9, "特殊公式", "=H8+1")
        add_arm(book, 10, "公式结果", "=1e-75*1e-75")
        add_arm(book, 11, "大正指数", "1e+2000000")
        add_arm(book, 12, "有效科学计数法", "3.21e-14")
        cartons = book.create_sheet("纸箱")
        cartons.append(["编号", "规格", "单价"])
        cartons.append(["BOX-TEST", "20*10*5", "0e-200000"])
        result = self.parse(book, {"摆臂成本核算": {"H9": "0e-200000"}})
        items = result["records"][0]["items"]
        self.assertEqual([item["amount"] for item in items], [None, None, None, None, "0.0000000000000321"])
        self.assertTrue(all(item["review_note"] for item in items[:4]))
        self.assertEqual(items[0]["source_info"]["raw"]["H"], "1e-200000")
        self.assertEqual(items[1]["source_info"]["cached"]["H"], "0e-200000")
        self.assertIsNone(result["references"]["cartons"][0]["unit_price"])
        self.assertLess(len(json.dumps(result)), 30_000)

    def test_part_code_only_row_is_retained_as_missing_cost(self):
        book = arm_book()
        add_arm(book, 8, None, None, G="PART-1")
        result = self.parse(book)
        self.assertEqual(result["summary"]["items"], 1)
        self.assertEqual(result["summary"]["missing_amount_items"], 1)
        self.assertEqual(result["records"][0]["items"][0]["part_code"], "PART-1")


if __name__ == "__main__":
    unittest.main()
