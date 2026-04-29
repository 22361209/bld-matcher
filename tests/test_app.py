from __future__ import annotations

import io
import os
import re
import sys
import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_web_module():
    spec = spec_from_file_location("bld_matcher_test_web", PROJECT_ROOT / "app.py")
    module = module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["bld_matcher_test_web"] = module
    spec.loader.exec_module(module)
    return module


class WebAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.root = root
        os.environ["SECRET_KEY"] = "test-secret"
        os.environ["MAX_UPLOAD_MB"] = "20"
        os.environ["BLD_DATA_DIR"] = str(root / "data")
        os.environ["BLD_UPLOAD_DIR"] = str(root / "uploads")
        os.environ["BLD_OUTPUT_DIR"] = str(root / "outputs")
        os.environ["DEFAULT_ADMIN_PASSWORD"] = "test-admin-pw"
        cls.web = load_web_module()
        cls.web.app.config["TESTING"] = True
        cls.client = cls.web.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def login(self):
        return self.client.post(
            "/login",
            data={"username": "007", "password": "test-admin-pw", "next": "/"},
            follow_redirects=False,
        )

    def test_login_and_homepage(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)

        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("BLD", html)
        self.assertLess(html.index('class="messages'), html.index('class="inquiry-landing"'))

    def test_core_admin_pages_load(self):
        self.login()
        for path in ["/products", "/materials", "/users", "/logs", "/system-updates"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_system_updates_page_reads_handoff_notes(self):
        self.login()
        response = self.client.get("/system-updates")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("系统更新", html)
        self.assertIn("当前最近重要变更", html)
        self.assertIn("项目交接说明.md", html)
        self.assertIn("ac3aa1a", html)
        self.assertIn("新增系统更新页面", html)

    def test_upload_limit_is_20mb(self):
        self.assertEqual(self.web.app.config["MAX_CONTENT_LENGTH"], 20 * 1024 * 1024)

    def test_oversized_upload_redirects(self):
        self.login()
        big_file = io.BytesIO(b"x" * (20 * 1024 * 1024 + 1))
        response = self.client.post(
            "/catalog",
            data={"catalog": (big_file, "big.xlsx")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/products"))

    def test_migrations_are_recorded(self):
        with self.web.connect(self.web.DB_PATH) as conn:
            rows = conn.execute("SELECT id FROM schema_migrations ORDER BY id").fetchall()
        self.assertEqual([row["id"] for row in rows], ["001_audit_log_actor", "002_product_price_and_image"])

    def test_generated_files_are_scoped_to_user(self):
        self.login()
        response = self.client.post("/products/export", data={"status": "active", "export_format": "bld"})
        self.assertEqual(response.status_code, 200)
        response.close()

        user_output_dir = self.root / "outputs" / "u1-007"
        files = list(user_output_dir.glob("catalog-export-bld-007-*.xlsx"))
        self.assertEqual(len(files), 1)
        self.assertFalse(list((self.root / "outputs").glob("catalog-export-bld-007-*.xlsx")))

    def test_admin_homepage_shows_all_recent_outputs(self):
        output_root = self.root / "outputs"
        other_user_dir = output_root / "u99-other"
        other_user_dir.mkdir(parents=True, exist_ok=True)
        root_file = output_root / "old-root-result.xlsx"
        other_user_file = other_user_dir / "other-user-result.xlsx"
        catalog_file = output_root / "catalog-export-bld-history-sample.xlsx"
        material_file = output_root / "26年4月冲压生产计划260423料单.xlsx"
        root_file.write_bytes(b"legacy")
        other_user_file.write_bytes(b"other")
        catalog_file.write_bytes(b"catalog")
        material_file.write_bytes(b"materials")

        self.login()
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("操作用户", html)
        self.assertIn("old-root-result.xlsx", html)
        self.assertIn("other-user-result.xlsx", html)
        self.assertIn("other", html)
        self.assertNotIn("catalog-export-bld-history-sample.xlsx", html)
        self.assertNotIn("26年4月冲压生产计划260423料单.xlsx", html)

        response = self.client.get("/?history_q=other-user")
        html = response.get_data(as_text=True)
        self.assertIn("other-user-result.xlsx", html)
        self.assertNotIn("old-root-result.xlsx", html)

        response = self.client.get("/?history_q=other")
        html = response.get_data(as_text=True)
        self.assertIn("other-user-result.xlsx", html)
        self.assertNotIn("old-root-result.xlsx", html)

    def test_quick_oe_lookup_on_homepage(self):
        from app.database import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K6004LB",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "55270-2Z000",
                    "models": "Sportage",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/?quick_oe=55270-2Z000")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("快速 OE 查询", html)
        self.assertIn("K6004LB", html)
        self.assertIn("OE 精准命中", html)

    def test_manual_column_result_defers_excel_until_download(self):
        from app.database import upsert_product
        from openpyxl import Workbook, load_workbook

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K6004LC",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "55270-2Z001",
                    "models": "Sportage",
                    "active": "1",
                },
                actor="tester",
            )

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["客户号码", "数量"])
        sheet.append(["55270-2Z001", 1])
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        self.login()
        response = self.client.post(
            "/match",
            data={"inquiry": (buffer, "manual-column.xlsx")},
            content_type="multipart/form-data",
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("选择匹配列", html)
        self.assertIn("返回上一步", html)
        self.assertNotIn("返回首页", html)
        upload_match = re.search(r'name="upload_path" value="([^"]+)"', html)
        output_match = re.search(r'name="output_name" value="([^"]+)"', html)
        self.assertIsNotNone(upload_match)
        self.assertIsNotNone(output_match)
        upload_path = upload_match.group(1)
        output_name = output_match.group(1)
        output_path = self.root / "outputs" / "u1-007" / output_name

        result = self.client.post(
            "/match/column",
            data={
                "upload_path": upload_path,
                "original_filename": "manual-column.xlsx",
                "output_name": output_name,
                "match_column": "0",
            },
        )
        result_html = result.get_data(as_text=True)

        self.assertEqual(result.status_code, 200)
        self.assertIn("Excel 文件将在点击下载时生成", result_html)
        self.assertIn("下载 Excel", result_html)
        self.assertIn("返回上一步", result_html)
        self.assertNotIn("返回首页", result_html)
        self.assertIn("K6004LC", result_html)
        self.assertFalse(output_path.exists())

        back = self.client.post(
            "/match/column/back",
            data={
                "upload_path": upload_path,
                "original_filename": "manual-column.xlsx",
                "output_name": output_name,
                "match_column": "0",
            },
        )
        back_html = back.get_data(as_text=True)
        self.assertEqual(back.status_code, 200)
        self.assertIn("选择匹配列", back_html)
        self.assertIn("返回上一步", back_html)
        self.assertNotIn("返回首页", back_html)
        self.assertRegex(back_html, r'<option value="0"[^>]*selected')

        download = self.client.post(
            "/match/column/download",
            data={
                "upload_path": upload_path,
                "original_filename": "manual-column.xlsx",
                "output_name": output_name,
                "match_column": "0",
            },
        )
        self.assertEqual(download.status_code, 200)
        download.close()
        self.assertTrue(output_path.exists())

        generated = load_workbook(output_path)
        generated_sheet = generated.active
        self.assertEqual(generated_sheet.cell(1, 3).value, "BLD NO.")
        self.assertEqual(generated_sheet.cell(2, 3).value, "K6004LC")
        generated.close()

    def test_uploaded_files_are_scoped_to_user(self):
        self.login()
        response = self.client.post(
            "/prices/import/preview",
            data={"price_file": (io.BytesIO(b"not a real workbook"), "same-name.xlsx")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        uploads = list((self.root / "uploads" / "u1-007").glob("price-*-007-same-name.xlsx"))
        self.assertEqual(len(uploads), 1)

    def test_import_lock_blocks_parallel_imports(self):
        from app.locks import ImportLockError, import_lock

        with import_lock("tester", "测试导入"):
            with self.assertRaises(ImportLockError):
                with import_lock("other", "第二个导入"):
                    pass


if __name__ == "__main__":
    unittest.main()
