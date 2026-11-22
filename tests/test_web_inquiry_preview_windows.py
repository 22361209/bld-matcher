from io import BytesIO
from pathlib import Path
import re
from unittest.mock import patch

from tests.test_web_inquiry_headerless import FormFields, source_workbook
from tests.web_app_test_base import WebAppTestBase, strip_xlsx_dimension


class TestWebInquiryPreviewWindows(WebAppTestBase):
    def setUp(self):
        self.register_customer_and_product("预览测试", "K72110L")
        self.login()

    def upload(self, suffix, sheets):
        data = source_workbook(suffix, sheets)
        if suffix == ".xlsx":
            path = self.root / "no-dimension.xlsx"
            path.write_bytes(data)
            strip_xlsx_dimension(path)
            data = path.read_bytes()
        response = self.client.post("/match", data={"inquiry": (BytesIO(data), "分页询价" + suffix)})
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        return html, FormFields(html).forms["/match/column"]

    def request_rows(self, form, **parameters):
        return self.client.post("/match/column/back", headers={"Accept": "application/json"}, data={
            "preview_action": "rows", "csrf_token": form["csrf_token"], "upload_path": form["upload_path"],
            "sheet_number": 1, "start_row": 1, **parameters,
        })

    def test_legacy_api_error_preview_remains_five_rows_by_eight_columns(self):
        data = source_workbook(".xlsx", {"第一张表": [["自定义标题"] * 10] * 9, "第二张表": [("另表内容",)]})
        response = self.client.post(
            "/api/internal/inquiry/analyze", data={"file": (BytesIO(data), "旧接口预览.xlsx")},
            headers={"Authorization": f"Bearer {self.create_internal_api_token()}"},
        )
        self.assertEqual(response.status_code, 422)
        preview = response.get_json()["column_preview"]
        self.assertEqual(set(preview), {"sheet", "columns", "rows"})
        self.assertEqual(preview["sheet"], "第一张表")
        self.assertEqual(len(preview["columns"]), 8)
        self.assertEqual(preview["rows"], [["自定义标题"] * 8] * 5)

    def test_scroll_from_deep_header_to_both_ends_with_sparse_distant_column(self):
        rows = [()] * 337
        rows[0], rows[147], rows[148] = ("客户询价单",), ("OE号",), ("OE-72110",)
        rows[302] = ("真实源值",) + (None,) * 25 + (" 远列\n<左 & 右> ",)
        rows[-1] = ("末行",)
        for suffix in (".xls", ".xlsx"):
            with self.subTest(suffix=suffix):
                html, form = self.upload(suffix, {"国内询价": rows, "海外询价": [("独立工作表",)]})
                first_table = html.split("</table>", 1)[0]
                initial = list(map(int, re.findall(r'data-preview-row="(\d+)"', first_table)))
                self.assertIn('data-column-label="AA"', first_table)
                self.assertIn("自动识别第 148 行为表头", html)
                self.assertEqual(initial, list(range(initial[0], initial[-1] + 1)))
                self.assertIn(148, initial)
                self.assertGreater(initial[0], 1)
                self.assertLessEqual(len(initial), 100)
                self.assertLess(initial[-1], 303)

                windows = [(max(1, end - 99), end) for end in range(initial[0] - 1, 0, -100)]
                windows += [(start, min(start + 99, len(rows))) for start in range(initial[-1] + 1, len(rows) + 1, 100)]
                received = {}
                for start, end in windows:
                    response = self.request_rows(form, start_row=start, end_row=end)
                    self.assertEqual(response.status_code, 200)
                    page = response.get_json()
                    self.assertEqual((page["sheet_number"], page["row_start"], page["row_end"]), (1, start, end))
                    self.assertEqual([row["number"] for row in page["rows"]], list(range(start, end + 1)))
                    received.update({row["number"]: row["cells"] for row in page["rows"]})
                self.assertEqual(sorted([*initial, *received]), list(range(1, len(rows) + 1)))
                self.assertEqual(received[1], {"0": "客户询价单"})
                self.assertEqual(received[302], {})
                self.assertEqual(received[303], {"0": "真实源值", "26": " 远列\n<左 & 右> "})
                self.assertEqual(received[337], {"0": "末行"})
                other = self.request_rows(form, sheet_number=2)
                self.assertEqual(other.status_code, 200)
                self.assertEqual(other.get_json()["rows"], [{"number": 1, "cells": {"0": "独立工作表"}}])

    def test_preview_windows_require_current_upload_permission_and_valid_range(self):
        from app.modules.admin.persistence import save_user

        _, form = self.upload(".xlsx", {"样表": [("私有型号-72110",)]})
        for params in ({"sheet_number": 2}, {"start_row": 0}, {"end_row": 101}, {"start_row": 2, "end_row": 1},
                       {"start_row": 1048577}):
            with self.subTest(params=params):
                self.assertEqual(self.request_rows(form, **params).status_code, 400)
        missing = Path(form["upload_path"]).with_name("missing.xlsx")
        self.assertEqual(self.request_rows(form, upload_path=str(missing)).status_code, 404)
        with patch.dict(self.web.app.config, {"TESTING": False}):
            self.assertEqual(self.request_rows(form, csrf_token="").status_code, 400)
            self.assertEqual(self.request_rows(form).status_code, 200)
        for effect, expected in (("allow", 404), ("deny", 403)):
            username = "preview-window-" + effect
            with self.web.connect(self.web.DB_PATH) as conn:
                save_user(conn, {"username": username, "password": "preview-test", "role": "user",
                                 "permission_overrides": {"generate_match": effect}}, actor="tester")
            self.client.post("/logout")
            self.client.post("/login", data={"username": username, "password": "preview-test"})
            forbidden = self.request_rows(form)
            self.assertEqual(forbidden.status_code, expected)
            self.assertNotIn("私有型号-72110", forbidden.get_data(as_text=True))
