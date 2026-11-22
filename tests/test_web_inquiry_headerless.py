from io import BytesIO
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from openpyxl import Workbook, load_workbook
import xlrd
import xlwt

from tests.test_web_inquiry_source_columns import SourceColumnsParser
from tests.web_app_test_base import WebAppTestBase


SHEETS = {
    "两行无表头": [("OE-72110", "99872-80001"), ("另行订购", "OE-72110")],
    "单行无表头": [(15, "OE-72110")],
    "标准询价": [("数量", "OE号"), (7, "OE-72110")],
}


def source_workbook(suffix, sheets=SHEETS):
    buffer = BytesIO()
    if suffix == ".xlsx":
        book = Workbook()
        book.remove(book.active)
        for name, rows in sheets.items():
            sheet = book.create_sheet(name)
            for row in rows:
                sheet.append(row)
        book.save(buffer)
        book.close()
    else:
        book = xlwt.Workbook()
        for name, rows in sheets.items():
            sheet = book.add_sheet(name)
            for row_index, row in enumerate(rows):
                for col_index, value in enumerate(row):
                    sheet.write(row_index, col_index, value)
        book.save(buffer)
    return buffer.getvalue()


def exported_rows(content, suffix):
    if suffix == ".xls":
        book = xlrd.open_workbook(file_contents=content)
        return {sheet.name: [sheet.row_values(i) for i in range(sheet.nrows)] for sheet in book.sheets()}
    book = load_workbook(BytesIO(content))
    try:
        return {sheet.title: list(sheet.values) for sheet in book}
    finally:
        book.close()


class FormFields(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = {}
        self.form = None
        self.select = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form" and "action" in attrs:
            self.form = self.forms.setdefault(attrs["action"], {})
        if self.form is None:
            return
        name = attrs.get("name")
        if tag == "input" and name:
            if attrs.get("type") not in ("checkbox", "radio") or "checked" in attrs:
                self.form[name] = attrs.get("value", "")
        elif tag == "select":
            self.select = name
        elif tag == "option" and self.select:
            if self.select not in self.form or "selected" in attrs:
                self.form[self.select] = attrs.get("value", "")
        elif tag == "button" and "formaction" in attrs:
            self.forms[attrs["formaction"]] = self.form

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None
        if tag == "form":
            self.form = None


class TestWebInquiryHeaderless(WebAppTestBase):
    def setUp(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(conn, {"bld_no": "K72110L", "oe_no_1": "OE-72110", "price_cny": 20, "active": "1"},
                           actor="tester")
        self.login()

    def test_auto_header_preview_scrolls_to_the_detected_row_for_selected_column(self):
        rows = [("询价单", None), ("日期", "2026-11-22"), (None, "PART NO."), (None, "OE-72110"),
                *[(None, None)] * 218, ("OE号", None), ("OE-72110", None)]
        for suffix in (".xlsx", ".xls"):
            with self.subTest(suffix=suffix):
                data = source_workbook(suffix, {"询价": rows})
                reply = self.client.post("/match", data={"inquiry": (BytesIO(data), "预览" + suffix)})
                for match_column, header_row in (("0", 223), ("1", 3)):
                    self.assertEqual(reply.status_code, 200)
                    html = reply.get_data(as_text=True)
                    fields = FormFields(html).forms["/match/column/back"]
                    self.assertEqual((fields["match_columns"], fields["header_row_1"]), (match_column, ""))
                    self.assertIn(f"自动识别第 {header_row} 行为表头，从第 {header_row + 1} 行开始匹配。", html)
                    marked = r'<tr\b(?=[^>]*class="[^"]*\binquiry-header-row\b)[^>]*data-preview-row="(\d+)"'
                    self.assertEqual(re.findall(marked, html), [str(header_row)])
                    self.assertIn(f'data-preview-row="{header_row + 1}"', html)
                    self.assertNotIn(f'data-preview-row="{3 if header_row == 223 else 223}"', html)
                    if match_column == "0":
                        reply = self.client.post("/match/column/back", data={**fields, "match_columns": "1"})

    def test_uploaded_first_oe_stays_a_data_row_in_matching_pricing_and_download(self):
        for suffix in (".xlsx", ".xls"):
            with self.subTest(suffix=suffix):
                original = source_workbook(suffix)
                filename = "inquiry" + suffix
                preview = self.client.post("/match", data={"inquiry": (BytesIO(original), filename)})
                self.assertEqual(preview.status_code, 200)
                preview_html = preview.get_data(as_text=True)
                self.assertRegex(preview_html, r'<input\b(?=[^>]*name="match_columns")(?=[^>]*value="1")[^>]*>')
                form = FormFields(preview_html).forms["/match/column"]
                path = form["upload_path"]
                form["match_columns"] = "1"
                response = self.client.post("/match/column", data=form)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertEqual(re.findall(r"共 \d+ 行，命中 \d+ 行，未找到 \d+ 行", html),
                                 ["共 4 行，命中 3 行，未找到 1 行"])
                result = SourceColumnsParser(html)
                self.assertEqual(
                    [row[f"source-{sheet}-B"] for row, sheet in zip(result.rows, (1, 1, 2, 3), strict=True)],
                    ["99872-80001", "OE-72110", "OE-72110", "OE-72110"],
                )
                self.assertNotIn("99872-80001", result.headings["source-1-B"])
                self.assertNotIn("OE-72110", result.headings["source-2-B"])
                self.assertIn("B · OE号", result.headings["source-2-B"])
                self.assertIn("OE号", result.headings["source-3-B"])
                self.assertEqual(re.findall(r'data-adjustment-key="([^"]+)"', html),
                                 ["sheet:1:row:2", "sheet:2:row:1", "sheet:3:row:2"])

                adjustments = {
                    "sheet:1:row:2": {"expected_bld_no": "K72110L", "tax_price": "33.17"},
                    "sheet:2:row:1": {"expected_bld_no": "K72110L", "tax_price": "47.25"},
                }
                with self.client.post("/match/download", data={
                    **form, "price_mode": "tax", "inquiry_adjustments": json.dumps(adjustments)
                }) as download:
                    self.assertEqual(download.status_code, 200)
                    saved = exported_rows(download.get_data(), suffix)
                for name, rows in saved.items():
                    self.assertIn("OE", rows[0][1])
                    self.assertEqual(list(rows[0][2:6]), ["BLD NO.", "含税单价", "产品状态", "匹配说明"])
                    expected_data = SHEETS[name][1:] if name == "标准询价" else SHEETS[name]
                    self.assertEqual([tuple(row[:2]) for row in rows[1:]], expected_data)
                self.assertFalse(saved["两行无表头"][1][2])
                self.assertEqual(list(saved["两行无表头"][2][2:4]), ["K72110L", 33.17])
                self.assertEqual(list(saved["单行无表头"][1][2:4]), ["K72110L", 47.25])
                self.assertEqual(list(saved["标准询价"][0][:2]), ["数量", "OE号"])
                self.assertEqual(list(saved["标准询价"][1][2:4]), ["K72110L", 20])
                self.assertEqual(Path(path).read_bytes(), original)

    def test_each_sheet_header_setting_survives_back_requery_and_quote_download(self):
        from app.modules.products.persistence import upsert_product

        self.register_customer_and_product("表头测试客户", "K72110L")
        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(conn, {"bld_no": "K72112L", "oe_no_1": "ITEM", "price_cny": 19, "active": "1"},
                           actor="tester")
        cover = [("客户询价单", None), ("出具日期", "2026-11-22"), (None, None)]
        sheets = {
            "手工表头": [*cover, ("旧编号", "OE-72110"), *[(None, None)] * 18,
                      ("客户编码", "客户参考"), ("客户甲", "OE-72110")],
            "强制无表头": [("客户乙", "ITEM"), ("客户丙", "OE-72110")],
            "自动识别": [*cover, ("客户编码", "OE号"), ("客户丁", "OE-72110")],
        }
        for suffix in (".xlsx", ".xls"):
            with self.subTest(suffix=suffix):
                original = source_workbook(suffix, sheets)
                upload = self.client.post("/match", data={"inquiry": (BytesIO(original), "逐表表头" + suffix)})
                self.assertEqual(upload.status_code, 200)
                form = FormFields(upload.get_data(as_text=True)).forms["/match/column"]
                self.assertEqual([form[f"header_row_{i}"] for i in (1, 2, 3)], ["", "", ""])
                form.update(match_columns="1", customer_code_column="0",
                            header_row_1="manual", header_number_1="23", header_row_2="0")
                page = self.client.post("/match/column", data=form)
                self.assertEqual(page.status_code, 200)
                html = page.get_data(as_text=True)
                expected_keys = ["sheet:1:row:24", "sheet:2:row:1", "sheet:2:row:2", "sheet:3:row:5"]
                self.assertEqual(re.findall(r'data-adjustment-key="([^"]+)"', html), expected_keys)
                self.assertIn("B · 客户参考", SourceColumnsParser(html).headings["source-1-B"])
                forms = FormFields(html).forms
                for action in ("/match/column/back", "/match/column", "/match/download",
                               "/match/write-quotes", "/match/drawings/download"):
                    self.assertEqual(forms[action]["header_row_1"], "23", action)
                    self.assertEqual(forms[action]["header_row_2"], "0", action)
                    self.assertFalse(forms[action].get("header_row_3"), action)

                back = self.client.post("/match/column/back", data=forms["/match/column/back"])
                preview_html = back.get_data(as_text=True)
                self.assertIn('data-preview-row="23"', preview_html)
                settings = FormFields(preview_html).forms["/match/column"]
                self.assertEqual((settings["header_row_1"], settings["header_number_1"],
                                  settings["header_row_2"], settings["header_row_3"]), ("manual", "23", "0", ""))
                again = self.client.post("/match/column", data=forms["/match/column"])
                self.assertEqual(re.findall(r'data-adjustment-key="([^"]+)"', again.get_data(as_text=True)), expected_keys)

                with self.client.post("/match/download", data={**forms["/match/download"], "price_mode": "tax"}) as file:
                    self.assertEqual(file.status_code, 200)
                    direct = exported_rows(file.get_data(), suffix)
                self.assertEqual(direct["手工表头"][22][2], "BLD NO.")
                self.assertEqual(direct["强制无表头"][1][2], "K72112L")

                quote = self.client.post("/match/write-quotes", headers={"Accept": "application/json"}, data={
                    **forms["/match/write-quotes"], "price_mode": "tax", "customer_name": "表头测试客户",
                    "inquiry_adjustments": json.dumps({
                        "sheet:2:row:1": {"expected_bld_no": "K72112L", "tax_price": "36.17"}
                    }),
                })
                self.assertEqual(quote.status_code, 200, quote.get_data(as_text=True))
                payload = quote.get_json()
                self.assertEqual((payload["written"], payload["skipped"]), (4, 0))
                with self.client.get(payload["download_url"]) as download:
                    self.assertEqual(download.status_code, 200)
                    saved = exported_rows(download.get_data(), suffix)
                manual, forced, auto = (saved[name] for name in sheets)
                self.assertEqual(manual[0][0], "客户询价单")
                self.assertEqual(manual[1][1], "2026-11-22")
                self.assertFalse(any(manual[2]))
                self.assertFalse(manual[3][2])
                self.assertEqual(list(manual[22][:3]), ["客户编码", "客户参考", "BLD NO."])
                self.assertEqual(list(manual[23][:4]), ["客户甲", "OE-72110", "K72110L", 20])
                self.assertEqual(list(forced[1][:4]), ["客户乙", "ITEM", "K72112L", 36.17])
                self.assertEqual(list(auto[3][:3]), ["客户编码", "OE号", "BLD NO."])
                self.assertEqual(Path(form["upload_path"]).read_bytes(), original)

                invalid = self.client.post("/match/column", data={**form, "header_number_1": "99"},
                                           follow_redirects=True)
                error_html = invalid.get_data(as_text=True)
                self.assertNotIn("data-inquiry-result-row", error_html)
                self.assertRegex(error_html, r"99[^<>]{0,80}(?:不存在|范围|超出|超过)")
