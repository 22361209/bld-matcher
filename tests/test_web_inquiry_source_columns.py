from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
from io import BytesIO
import re

from openpyxl import Workbook, load_workbook
import xlrd
import xlwt

from tests.web_app_test_base import WebAppTestBase


class SourceColumnsParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.options = {}
        self.visible_cols = []
        self.headings = {}
        self.rows = []
        self.cell_contents = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "input" and attrs.get("type") == "checkbox" and "data-source-column" in attrs:
            self.options[attrs["value"]] = "checked" in attrs
        if tag == "tr" and "data-inquiry-result-row" in attrs:
            self.rows.append({})
        column = attrs.get("data-col", "")
        if not column.startswith("source-") or "hidden" in attrs:
            return
        if tag == "col":
            self.visible_cols.append(column)
        if tag in {"th", "td"}:
            cells = self.headings if tag == "th" else self.rows[-1]
            cells[column] = ""
            self.cell_contents = (cells, column)

    def handle_data(self, data):
        if self.cell_contents:
            cells, column = self.cell_contents
            cells[column] += data

    def handle_endtag(self, tag):
        if tag in {"th", "td"}:
            self.cell_contents = None


class TestWebInquirySourceColumns(WebAppTestBase):
    def test_blank_match_heading_preserves_other_column_titles_including_empty_moq(self):
        self.register_customer_and_product("示例客户", "K78452L")
        self.login()
        name = "中文名称（是否错误，错误帮忙纠正，谢谢）"
        header = [None] * 8 + [name, "含税价", "起订量"]
        data = [None] * 5 + ["K78452L", None, None, "控制臂", 88]
        report = {
            "询价": [header, data],
            "有封面": [["客户询价清单"], [], header, data],
            "普通数据": [[None] * 8 + ["中文名称", "含税价", 99], data],
        }
        for extension in ("xls", "xlsx"):
            with self.subTest(extension=extension):
                book = xlwt.Workbook() if extension == "xls" else Workbook()
                if extension == "xlsx":
                    book.remove(book.active)
                for title, rows in report.items():
                    sheet = book.add_sheet(title) if extension == "xls" else book.create_sheet(title)
                    for r, row in enumerate(rows):
                        for c, value in enumerate(row):
                            if value is not None:
                                if extension == "xls":
                                    sheet.write(r, c, value)
                                else:
                                    sheet.cell(r + 1, c + 1, value)
                buffer = BytesIO()
                book.save(buffer)
                buffer.seek(0)
                upload = self.client.post("/match", data={"inquiry": (buffer, f"报价.{extension}")})
                path = re.search(r'name="upload_path" value="([^"]+)"', upload.get_data(as_text=True)).group(1)
                form = {"upload_path": path, "match_columns": "5"}
                response = self.client.post("/match/column", data=form)
                self.assertEqual(response.status_code, 200)
                result = SourceColumnsParser(response.get_data(as_text=True))
                for number in (1, 2):
                    for letter, label in zip("IJK", (name, "含税价", "起订量"), strict=True):
                        key = f"source-{number}-{letter}"
                        self.assertTrue(result.options[key])
                        self.assertIn(f"{letter} · {label}", result.headings[key])
                    self.assertEqual(result.rows[number - 1][f"source-{number}-K"], "")
                self.assertNotIn("source-3-K", result.headings)

                with self.client.post("/match/download", data=form) as download:
                    self.assertEqual(download.status_code, 200)
                    content = download.get_data()
                if extension == "xls":
                    saved = xlrd.open_workbook(file_contents=content)
                    self.assertEqual(saved.sheet_by_name("询价").cell_value(0, 11), "BLD NO.")
                    self.assertEqual(saved.sheet_by_name("有封面").cell_value(2, 11), "BLD NO.")
                else:
                    saved = load_workbook(BytesIO(content))
                    self.assertEqual(saved["询价"]["L1"].value, "BLD NO.")
                    self.assertEqual(saved["有封面"]["L3"].value, "BLD NO.")
                    saved.close()

                forced = self.client.post("/match/column", data={**form, "header_row_1": "0"})
                forced_result = SourceColumnsParser(forced.get_data(as_text=True))
                self.assertNotIn("source-1-K", forced_result.options)
                self.assertIn("source-2-K", forced_result.options)

    def open_result(self, workbook, filename):
        self.register_customer_and_product("示例客户", "K78452L")
        self.login()
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        preview = self.client.post("/match", data={"inquiry": (buffer, filename)})
        self.assertEqual(preview.status_code, 200)
        preview_html = preview.get_data(as_text=True)
        self.assertRegex(preview_html, r'<th\b[^>]*data-column-label="AA"')
        self.assertNotIn("AZ 列", preview_html)
        self.assertNotIn("IV 列", preview_html)
        upload_path = re.search(r'name="upload_path" value="([^"]+)"', preview_html)
        self.assertIsNotNone(upload_path)
        form = {"upload_path": upload_path.group(1), "original_filename": filename, "match_columns": "0"}
        result = self.client.post("/match/column", data=form)
        self.assertEqual(result.status_code, 200)
        return result.get_data(as_text=True), form

    def test_xlsx_source_columns_follow_sheet_and_row_including_unmatched(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "国内询价"
        sheet.append([None, "询价清单"])
        sheet.append(["OE号", "数量", "备注", "备注", None, "交货日期"])
        sheet.append(["K78452L", 0, "左", "右", "00023", date(2026, 11, 22)])
        sheet.append(["未命中-73851", 12])
        note = " 前排\n<示例> & '核对' "
        sheet["AA4"] = note
        sheet.column_dimensions["AA"].hidden = True
        sheet["AZ2"] = "　"
        sheet["AZ3"] = " \t "
        sheet["IV3"].number_format = "0.00"
        second = book.create_sheet("海外询价")
        second.append(["OE号", "包装"])
        second.append(["K78452L", "原箱"])
        book.create_sheet("空白模板").append(["OE号", "模板说明"])

        html, form = self.open_result(book, "source-columns.xlsx")
        self.assertIn("共 3 行，命中 2 行，未找到 1 行", html)
        self.assertIn("data-open-source-columns", html)
        self.assertIn("国内询价", html)
        self.assertIn("海外询价", html)
        self.assertNotIn("空白模板", html)
        result = SourceColumnsParser(html)
        expected = [f"source-1-{letter}" for letter in ("A", "B", "C", "D", "E", "F", "AA")]
        expected.extend(["source-2-A", "source-2-B"])
        self.assertEqual(result.options, dict.fromkeys(expected, True))
        self.assertEqual(result.visible_cols, expected)
        self.assertEqual(list(result.headings), expected)
        self.assertIn("B · 数量", result.headings["source-1-B"])
        self.assertIn("B · 包装", result.headings["source-2-B"])
        self.assertIn("C · 备注", result.headings["source-1-C"])
        self.assertIn("D · 备注", result.headings["source-1-D"])
        source_values = [
            {"source-1-A": "K78452L", "source-1-B": "0", "source-1-C": "左",
             "source-1-D": "右", "source-1-E": "00023", "source-1-F": "2026-11-22"},
            {"source-1-A": "未命中-73851", "source-1-B": "12", "source-1-AA": note},
            {"source-2-A": "K78452L", "source-2-B": "原箱"},
        ]
        self.assertEqual(result.rows, [dict.fromkeys(expected, "") | values for values in source_values])

        download = self.client.post("/match/download", data=form)
        self.assertEqual(download.status_code, 200)
        generated = load_workbook(BytesIO(download.get_data()))
        download.close()
        try:
            self.assertEqual(generated["国内询价"]["AA4"].value, note)
            self.assertEqual(generated["国内询价"]["G3"].value, "K78452L")
            self.assertEqual(generated["海外询价"]["B2"].value, "原箱")
        finally:
            generated.close()

    def test_xls_source_columns_preserve_cell_types_and_blank_headers(self):
        book = xlwt.Workbook()
        sheet = book.add_sheet("旧询价单")
        for index, label in enumerate(["OE号", "库存", "已确认", "交期"]):
            sheet.write(0, index, label)
        sheet.write(1, 0, "K78452L")
        sheet.write(1, 1, 0)
        sheet.write(1, 2, False)
        sheet.write(1, 3, date(2026, 11, 22), xlwt.easyxf(num_format_str="YYYY-MM-DD"))
        sheet.write(1, 26, "00007")
        sheet.col(26).hidden = True
        sheet.write(0, 51, "　")
        sheet.write(1, 51, " \t ")
        sheet.write(1, 255, None, xlwt.easyxf(num_format_str="0.00"))
        html, _ = self.open_result(book, "source-columns.xls")
        result = SourceColumnsParser(html)
        self.assertIn("旧询价单", html)
        self.assertEqual(result.visible_cols, ["source-1-A", "source-1-B", "source-1-C", "source-1-D", "source-1-AA"])
        self.assertEqual(result.options, dict.fromkeys(result.visible_cols, True))
        self.assertIn("AA", result.headings["source-1-AA"])
        self.assertEqual(result.rows, [{
            "source-1-A": "K78452L", "source-1-B": "0", "source-1-C": "FALSE",
            "source-1-D": "2026-11-22", "source-1-AA": "00007",
        }])
