from __future__ import annotations

import io
import shutil
from unittest.mock import patch

from openpyxl import load_workbook

from tests.web_app_test_base import WebAppTestBase, PROJECT_ROOT
from tests.node_test_helper import run_node_test


class CatalogBrandVisibilityTest(WebAppTestBase):
    def setUp(self):
        self.login()
        self.addCleanup(self.cleanup_products, "BRANDVIS-%")
        from app.modules.products.persistence import upsert_product
        with self.web.connect(self.web.DB_PATH) as connection:
            for number, brand in enumerate(("TOYOTA\nLEXUS", "VOLKSWAGEN", "GMC\nDS\nUNKNOWN BRAND")):
                upsert_product(connection, {"bld_no": f"BRANDVIS-{number}", "series": brand,
                    "item": "Front Lower Control Arm", "item_zh": "前下控制臂", "models": "Original English",
                    "models_zh": "独立中文", "oe_no_1": "FICTITIOUS-OE"}, actor="test")
            connection.commit()

    def record(self, number=0):
        with self.web.connect(self.web.DB_PATH) as connection:
            return dict(connection.execute("SELECT * FROM products WHERE bld_no=?", (f"BRANDVIS-{number}",)).fetchone())

    def toggle(self, record, language, enabled, **extra):
        return self.client.post(f"/products/{record['id']}/catalog-visibility", data={
            "language": language, "visible": str(int(enabled)),
            "expected_visible": str(record[f"catalog_visible_{language}"]),
            "expected_updated_at": record["updated_at"], **extra,
        }, headers={"Accept": "application/json"})

    def test_brand_labels_cover_rows_mobile_details_filters_and_unknown_fallback(self):
        for language, brand in (("en", "TOYOTA\nLEXUS"), ("zh", "丰田\n雷克萨斯")):
            html = self.client.get("/products/fragment", query_string={"bld": "BRANDVIS-", "lang": language}).text
            self.assertIn(f'data-col="series">{brand}</td>', html)
            self.assertIn(f'product-mobile-card-brand" title="{brand}"', html)
            self.assertIn(f'<dt>品牌</dt><dd>{brand}</dd>', html)
            self.assertIn('value="TOYOTA"', html)
            self.assertIn('value="VOLKSWAGEN"', html)
            if language == "zh":
                self.assertIn("<span>丰田</span>", html)
                self.assertIn("<span>大众</span>", html)
            self.assertIn('data-col="series">GMC\nDS\nUNKNOWN BRAND</td>', html)
        empty = self.client.get("/products/fragment", query_string={"bld": "NO-SUCH-BRANDVIS", "lang": "zh", "brand": "TOYOTA"}).text
        self.assertIn("<span>丰田</span>", empty)
        self.assertEqual(self.record()["series"], "TOYOTA\nLEXUS")

    def test_both_export_layouts_and_languages_preserve_original_grouping_keys(self):
        for layout in ("bld", "brand"):
            for language in ("en", "zh"):
                response = self.client.post("/products/export", data={"bld": "BRANDVIS-", "export_format": layout, "export_language": language})
                self.addCleanup(response.close)
                self.assertEqual(response.status_code, 200)
                workbook = load_workbook(io.BytesIO(response.data))
                rows = list(workbook.active.values)
                header = rows[0]
                series_column = header.index("品牌" if language == "zh" else ("SERIES" if layout == "brand" else "Brand"))
                values = {row[series_column] for row in rows[1:]}
                self.assertIn("丰田\n雷克萨斯" if language == "zh" else "TOYOTA\nLEXUS", values)
                self.assertIn("大众" if language == "zh" else "VOLKSWAGEN", values)
                self.assertIn("GMC\nDS\nUNKNOWN BRAND", values)
                if layout == "brand":
                    self.assertEqual([row[header.index("BLD NO.")] for row in rows[1:]], ["BRANDVIS-2", "BRANDVIS-0", "BRANDVIS-1"])
                workbook.close()

    def test_single_flag_update_preserves_every_other_field_and_audits(self):
        before = self.record()
        response = self.toggle(before, "zh", False, item="must not write", price_cny="999")
        self.assertEqual(response.status_code, 200)
        after = self.record()
        for field in before:
            if field not in {"updated_at", "catalog_visible_zh"}:
                self.assertEqual(after[field], before[field], field)
        self.assertEqual(after["catalog_visible_zh"], 0)
        self.assertNotEqual(after["updated_at"], before["updated_at"])
        with self.web.connect(self.web.DB_PATH) as connection:
            self.assertTrue(connection.execute("SELECT 1 FROM audit_logs WHERE action='修改目录可见性' AND target_key='BRANDVIS-0'").fetchone())

    def test_stale_requests_independent_flags_hidden_management_and_exports(self):
        initial = self.record()
        self.assertEqual(self.toggle(initial, "zh", False).status_code, 200)
        self.assertEqual(self.toggle(initial, "zh", True).status_code, 409)
        self.assertEqual(self.toggle(self.record(), "en", False).status_code, 200)
        self.assertEqual((self.record()["catalog_visible_en"], self.record()["catalog_visible_zh"]), (0, 0))
        for language in ("en", "zh"):
            html = self.client.get("/products/fragment", query_string={"bld": "BRANDVIS-0", "lang": language}).text
            self.assertNotIn('data-product-id="' + str(initial["id"]) + '"', html)
            export = self.client.post("/products/export", data={"bld": "BRANDVIS-0", "export_language": language})
            self.assertEqual(export.status_code, 302)
        for scope in ("all", "neither"):
            html = self.client.get("/products/fragment", query_string={"bld": "BRANDVIS-0", "visibility": scope}).text
            self.assertIn("BRANDVIS-0", html)

    def test_permission_failure_is_read_only_and_csrf_is_required(self):
        from app.modules.admin.persistence import save_user
        before = self.record()
        with self.web.connect(self.web.DB_PATH) as connection:
            save_user(connection, {"username": "brandvis-viewer", "display_name": "Viewer", "password": "fake-viewer-pw", "role": "viewer", "active": "1"}, actor="test")
            connection.commit()
        self.client.post("/logout")
        self.client.post("/login", data={"username": "brandvis-viewer", "password": "fake-viewer-pw"})
        html = self.client.get("/products", query_string={"bld": "BRANDVIS-0"}).text
        self.assertIn('data-can-edit="0"', html)
        self.assertIn("只读", html)
        self.assertEqual(self.toggle(before, "zh", False).status_code, 403)
        self.assertEqual(self.record(), before)
        self.login()
        self.client.get("/products", query_string={"bld": "BRANDVIS-0"})
        with self.client.session_transaction() as session:
            csrf = session["_csrf_token"]
        self.web.app.config["TESTING"] = False
        try:
            self.assertEqual(self.toggle(before, "zh", False).status_code, 400)
            self.assertEqual(self.toggle(before, "zh", False, csrf_token=csrf).status_code, 200)
        finally:
            self.web.app.config["TESTING"] = True

    def test_validation_and_failed_audit_roll_back_visibility(self):
        before = self.record()
        self.assertEqual(self.toggle(before, "en", False, visible="invalid").status_code, 400)
        with patch("app.modules.products.repository.log_event", side_effect=RuntimeError("test audit failure")):
            self.assertEqual(self.toggle(before, "en", False).status_code, 500)
        self.assertEqual(self.record(), before)

    def test_visibility_request_state_with_node(self):
        if not shutil.which("node"):
            self.skipTest("Node.js unavailable")
        run_node_test(self, PROJECT_ROOT / "tests/js/catalog_visibility.test.mjs", project_root=PROJECT_ROOT)
