from __future__ import annotations

from tests.web_app_test_base import (
    WebAppTestBase,
    io,
    re,
)


class TestWebInquiryInteractions(WebAppTestBase):
    def test_match_download_with_prices_requires_manage_customer_prices(self):
        from app.modules.admin.persistence import save_user
        from app.modules.products.persistence import upsert_product
        from openpyxl import Workbook

        self.addCleanup(self.cleanup_products, "K-PRICE-GATE-%")
        username = "price-gate-user"

        def cleanup_user():
            self.client.post("/logout")
            with self.web.connect(self.web.DB_PATH) as connection:
                row = connection.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()
                if row:
                    connection.execute(
                        "DELETE FROM user_permission_overrides WHERE user_id = ?", (row["id"],)
                    )
                    connection.execute("DELETE FROM users WHERE id = ?", (row["id"],))
                    connection.commit()

        self.addCleanup(cleanup_user)
        self.login()
        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-PRICE-GATE-001",
                    "series": "TEST",
                    "item": "Price Gate Arm",
                    "oe_no_1": "PRICE-GATE-OE",
                    "price_cny": "66.00",
                    "active": "1",
                },
                actor="tester",
            )
            save_user(
                conn,
                {"username": username, "password": "price-gate-pw", "role": "user", "active": "1"},
                actor="tester",
            )
            conn.commit()

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["OE号"])
        sheet.append(["PRICE-GATE-OE"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        self.client.post("/logout")
        login = self.client.post(
            "/login",
            data={"username": username, "password": "price-gate-pw", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)
        response = self.client.post(
            "/match",
            data={"inquiry": (buffer, "price-gate.xlsx")},
            content_type="multipart/form-data",
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        upload_match = re.search(r'name="upload_path" value="([^"]+)"', html)
        self.assertIsNotNone(upload_match)

        download = self.client.post(
            "/match/download",
            data={
                "upload_path": upload_match.group(1),
                "original_filename": "price-gate.xlsx",
                "price_mode": "tax",
            },
            follow_redirects=False,
        )
        self.assertEqual(download.status_code, 302)
        self.assertTrue(download.headers["Location"].endswith("/"))

    def test_quick_inquiry_results_can_filter_by_match_source(self):
        from app.modules.products.persistence import upsert_product

        self.login()
        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "QF6010B",
                    "series": "TEST",
                    "item": "BLD FILTER HIT",
                    "oe_no_1": "OE-BLD-FILTER",
                    "active": "1",
                },
                actor="tester",
            )
            upsert_product(
                conn,
                {
                    "bld_no": "QF-OE-HIT",
                    "series": "TEST",
                    "item": "OE FILTER HIT",
                    "oe_no_1": "QF6010-OE",
                    "active": "1",
                },
                actor="tester",
            )
            upsert_product(
                conn,
                {
                    "bld_no": "QF-BRAND-HIT",
                    "series": "TEST",
                    "item": "BRAND FILTER HIT",
                    "oe_no_2": "QF6010-BRAND",
                    "active": "1",
                },
                actor="tester",
            )
            conn.commit()

        response = self.client.get("/?quick_oe=QF6010")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("只看BLD号", html)
        self.assertIn("只看OE号", html)
        self.assertIn("只看品牌号", html)
        self.assertIn("QF6010B", html)
        self.assertIn("QF-OE-HIT", html)
        self.assertIn("QF-BRAND-HIT", html)
        self.assertIn("命中BLD号：", html)
        self.assertIn("命中OE号：", html)
        self.assertIn("命中品牌号：", html)
        self.assertIn("QF6010-OE", html)
        self.assertIn("QF6010-BRAND", html)
        self.assertIn('data-quick-results data-initial-filter=""', html)
        self.assertIn('data-match-type="bld"', html)
        self.assertIn('data-match-type="oe"', html)
        self.assertIn('data-match-type="brand"', html)

        response = self.client.get("/?quick_oe=QF6010&quick_filter=bld")
        html = response.get_data(as_text=True)
        self.assertIn('data-quick-results data-initial-filter="bld"', html)
        self.assertIn("QF6010B", html)
        self.assertIn("QF-OE-HIT", html)
        self.assertIn("QF-BRAND-HIT", html)

        response = self.client.get("/?quick_oe=QF6010&quick_filter=oe")
        html = response.get_data(as_text=True)
        self.assertIn('data-quick-results data-initial-filter="oe"', html)
        self.assertIn("QF6010B", html)
        self.assertIn("QF-OE-HIT", html)
        self.assertIn("QF-BRAND-HIT", html)

        response = self.client.get("/?quick_oe=QF6010&quick_filter=brand")
        html = response.get_data(as_text=True)
        self.assertIn('data-quick-results data-initial-filter="brand"', html)
        self.assertIn("QF6010B", html)
        self.assertIn("QF-BRAND-HIT", html)
        self.assertIn("QF-OE-HIT", html)

    def test_manual_map_json_appends_code_to_product_oe_list(self):
        from app.modules.products.persistence import upsert_product

        self.addCleanup(self.cleanup_products, "K-MAP-OE-%")
        self.login()
        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {"bld_no": "K-MAP-OE-001", "series": "TEST", "item": "Map OE Arm", "active": "1"},
                actor="tester",
            )
            conn.commit()

        response = self.client.post(
            "/manual-map",
            data={"source_code": "MAP-OE-CODE-1", "bld_no": "K-MAP-OE-001", "sync_target": "oe", "note": ""},
            headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["appended"])
        self.assertIn("人工映射已保存", payload["message"])
        self.assertIn("已同步加入产品目录OE 号", payload["message"])
        with self.web.connect(self.web.DB_PATH) as conn:
            product = conn.execute(
                "SELECT oe_no_1, oe_no_2 FROM products WHERE bld_no = ?",
                ("K-MAP-OE-001",),
            ).fetchone()
        self.assertIn("MAP-OE-CODE-1", product["oe_no_1"])
        self.assertNotIn("MAP-OE-CODE-1", product["oe_no_2"] or "")

    def test_manual_map_json_appends_code_to_product_brand_list(self):
        from app.modules.products.persistence import upsert_product

        self.addCleanup(self.cleanup_products, "K-MAP-BRAND-%")
        self.login()
        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {"bld_no": "K-MAP-BRAND-001", "series": "TEST", "item": "Map Brand Arm", "active": "1"},
                actor="tester",
            )
            conn.commit()

        response = self.client.post(
            "/manual-map",
            data={
                "source_code": "MAP-BRAND-CODE-1",
                "bld_no": "K-MAP-BRAND-001",
                "sync_target": "brand_code",
                "note": "",
            },
            headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["appended"])
        self.assertIn("已同步加入产品目录品牌号码", payload["message"])
        with self.web.connect(self.web.DB_PATH) as conn:
            product = conn.execute(
                "SELECT oe_no_1, oe_no_2 FROM products WHERE bld_no = ?",
                ("K-MAP-BRAND-001",),
            ).fetchone()
        self.assertIn("MAP-BRAND-CODE-1", product["oe_no_2"])
        self.assertNotIn("MAP-BRAND-CODE-1", product["oe_no_1"] or "")

    def test_manual_map_json_rejects_empty_codes(self):
        self.login()
        for data in (
            {"source_code": "", "bld_no": "K-MAP-OE-001"},
            {"source_code": "MAP-OE-CODE-2", "bld_no": ""},
        ):
            response = self.client.post(
                "/manual-map",
                data=data,
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )
            self.assertEqual(response.status_code, 400)
            payload = response.get_json()
            self.assertFalse(payload["ok"])
            self.assertIn("请输入客户号码和 BLD NO.", payload["error"])

    def test_manual_map_json_requires_manage_aliases_permission(self):
        from app.modules.admin.persistence import save_user

        with self.web.connect(self.web.DB_PATH) as conn:
            save_user(
                conn,
                {
                    "username": "map-plain-user",
                    "display_name": "Map Plain User",
                    "password": "plain-pw",
                    "role": "user",
                    "active": "1",
                },
                actor="tester",
            )

        self.client.post("/login", data={"username": "map-plain-user", "password": "plain-pw", "next": "/"})
        response = self.client.post(
            "/manual-map",
            data={"source_code": "MAP-DENIED-1", "bld_no": "K-MAP-OE-001"},
            headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 403)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        with self.web.connect(self.web.DB_PATH) as conn:
            alias = conn.execute(
                "SELECT id FROM aliases WHERE source_code = ?",
                ("MAPDENIED1",),
            ).fetchone()
        self.assertIsNone(alias)
        self.client.post("/logout")

    def test_manual_map_form_submit_still_redirects(self):
        self.login()
        response = self.client.post(
            "/manual-map",
            data={"source_code": "MAP-FORM-1", "bld_no": "K-MAP-OE-001"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_result_page_renders_map_oe_buttons_and_modal_for_unmatched_rows(self):
        from app.modules.products.persistence import upsert_product

        self.addCleanup(self.cleanup_products, "K-MAP-RENDER-%")
        self.login()
        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-MAP-RENDER-001",
                    "series": "TEST",
                    "item": "Map Render Arm",
                    "oe_no_1": "MAP-RENDER-OE",
                    "active": "1",
                },
                actor="tester",
            )
            conn.commit()

        response = self.client.post("/match", data={"quick_oe": "MAP-RENDER-OE\nMAP-RENDER-UNMATCHED"})
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-map-oe-code="MAP-RENDER-UNMATCHED"', html)
        self.assertNotIn('data-map-oe-code="MAP-RENDER-OE"', html)
        self.assertIn('id="map-oe-modal"', html)
        self.assertIn('name="sync_target" value="oe" checked', html)
        self.assertIn('name="sync_target" value="brand_code"', html)
        self.assertIn("/products/lookup", html)
        self.assertIn("/manual-map", html)

    def test_summary_row_lists_each_split_code_for_multi_code_unmatched_row(self):
        from app.modules.inquiry.excel.analysis import summary_row

        row = summary_row(1, "MAP-MULTI-AAA / MAP-MULTI-BBB", "Arm", None)
        self.assertEqual(row["unmatched_oe_codes"], ["MAP-MULTI-AAA", "MAP-MULTI-BBB"])
        single = summary_row(2, "MAP-MULTI-CCC", "Arm", None)
        self.assertEqual(single["unmatched_oe_codes"], [])

    def test_result_page_hides_map_oe_controls_without_manage_aliases(self):
        from app.modules.admin.persistence import save_user
        from app.modules.products.persistence import upsert_product

        self.addCleanup(self.cleanup_products, "K-MAP-PLAIN-%")
        with self.web.connect(self.web.DB_PATH) as conn:
            save_user(
                conn,
                {
                    "username": "map-hide-user",
                    "display_name": "Map Hide User",
                    "password": "plain-pw",
                    "role": "user",
                    "active": "1",
                },
                actor="tester",
            )
            upsert_product(
                conn,
                {"bld_no": "K-MAP-PLAIN-001", "series": "TEST", "item": "Map Plain Arm", "active": "1"},
                actor="tester",
            )
            conn.commit()

        self.client.post("/login", data={"username": "map-hide-user", "password": "plain-pw", "next": "/"})
        response = self.client.post("/match", data={"quick_oe": "K-MAP-PLAIN-001\nMAP-RENDER-UNMATCHED"})
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("MAP-RENDER-UNMATCHED", html)
        self.assertNotIn("data-map-oe-code", html)
        self.assertNotIn('id="map-oe-modal"', html)
        self.assertIn("<strong data-current-bld>K-MAP-PLAIN-001</strong>", html)
        self.assertNotIn("data-inquiry-bld-input", html)
        self.assertNotIn("data-inquiry-tax-price", html)
        self.assertNotIn('id="inquiry-bld-options"', html)
        self.client.post("/logout")

    def test_result_page_uses_shared_data_grid_protocol(self):
        from app.modules.products.persistence import upsert_product

        self.addCleanup(self.cleanup_products, "K-GRID-CHECK-%")
        self.login()
        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-GRID-CHECK-001",
                    "series": "TEST",
                    "item": "Grid Check Arm",
                    "oe_no_1": "GRID-CHECK-001",
                    "active": "1",
                },
                actor="tester",
            )
            conn.commit()

        response = self.client.post("/match", data={"quick_oe": "GRID-CHECK-001\nGRID-CHECK-002"})
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("data-resizable-grid", html)
        self.assertIn('data-grid-key="inquiry-result"', html)
        self.assertIn("data-grid-scroll", html)
        self.assertIn("data-column-storage-scope", html)
        self.assertEqual(html.count("<col data-col="), 9)
        for column in ("row", "oe", "customer-code", "bld", "image", "price", "status", "score", "reason"):
            self.assertIn(f'<col data-col="{column}">', html)
            self.assertIn(f'<th data-col="{column}">', html)
            self.assertIn(f'<td data-col="{column}"', html)
        self.assertEqual(html.count("data-column-drag-handle"), 9)
        self.assertIn("data-grid-footer", html)
        self.assertIn("data-grid-summary", html)
        self.assertIn("<strong>1–2</strong><span> / 2</span>", html)
        self.assertNotIn("重新查询", html)

    def test_quick_oe_lookup_on_homepage(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K6004LB",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "55270-2Z000",
                    "models": "Sportage",
                    "image_path": "product_images/K6004LB.jpg",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/?quick_oe=55270-2Z000")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("快速号码查询", html)
        self.assertIn("K6004LB", html)
        self.assertIn("OE 精准命中", html)
        self.assertIn("data-quick-oe-image", html)
        self.assertIn('id="quick-oe-image-modal"', html)

    def test_quick_inquiry_fragment_is_authenticated_and_returns_only_results(self):
        from app.modules.admin.persistence import save_user
        from app.modules.products.persistence import upsert_product

        self.client.post("/logout")
        denied = self.client.get(
            "/inquiry/quick-search",
            query_string={"quick_oe": "INLINE-OE-001"},
            headers={"Accept": "text/html", "X-Requested-With": "fetch"},
        )
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.get_json()["ok"], False)

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-INLINE-001",
                    "series": "HYUNDAI",
                    "item": "INLINE RESULT",
                    "oe_no_1": "INLINE-OE-001",
                    "models": "Sportage",
                    "active": "1",
                },
                actor="tester",
            )
            save_user(
                conn,
                {
                    "username": "viewer-inline-query",
                    "display_name": "Viewer Inline Query",
                    "password": "viewer-pw",
                    "role": "viewer",
                    "active": "1",
                },
                actor="tester",
            )
            conn.commit()

        self.login()
        homepage = self.client.get("/").get_data(as_text=True)
        self.assertIn("data-quick-results-host", homepage)
        self.assertIn('data-quick-results-url="/inquiry/quick-search"', homepage)

        response = self.client.get(
            "/inquiry/quick-search",
            query_string={"quick_oe": "INLINE-OE-001", "quick_filter": "oe"},
            headers={"Accept": "text/html", "X-Requested-With": "fetch"},
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("text/html"))
        self.assertIn('data-quick-results data-initial-filter="oe"', html)
        self.assertIn("K-INLINE-001", html)
        self.assertIn("OE 精准命中", html)
        self.assertNotIn("<!doctype html>", html.lower())
        self.assertNotIn("data-quick-results-host", html)

        self.assertEqual(self.client.get("/inquiry/quick-search").status_code, 400)
        self.assertEqual(
            self.client.get(
                "/inquiry/quick-search",
                query_string={"quick_oe": "A" * 5001},
            ).status_code,
            400,
        )

        self.client.post("/logout")
        login = self.client.post(
            "/login",
            data={"username": "viewer-inline-query", "password": "viewer-pw", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)
        forbidden = self.client.get(
            "/inquiry/quick-search",
            query_string={"quick_oe": "INLINE-OE-001"},
            headers={"Accept": "text/html", "X-Requested-With": "fetch"},
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.get_json()["ok"], False)
        self.client.post("/logout")
        self.login()

    def test_quick_brand_code_lookup_on_homepage(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K6004BR",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "55270-2Z010",
                    "oe_no_2": "MOOG：K623123",
                    "models": "Sportage",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        for query in ["623123", "K623123", "MOOG：K623123"]:
            with self.subTest(query=query):
                response = self.client.get("/", query_string={"quick_oe": query})
                html = response.get_data(as_text=True)

                self.assertEqual(response.status_code, 200)
                self.assertIn("快速号码查询", html)
                self.assertIn("K6004BR", html)
                self.assertIn("品牌号码精准命中", html)

    def test_quick_bld_lookup_on_homepage(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-BLD-LOOKUP",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "BLDLOOKUP-OE",
                    "models": "Sportage",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/", query_string={"quick_oe": "K-BLD-LOOKUP"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("快速号码查询", html)
        self.assertIn("K-BLD-LOOKUP", html)
        self.assertIn("BLD NO. 精准命中", html)

    def test_quick_bld_fragment_lookup_on_homepage(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            for bld_no in ["K6004LB", "K6004RB", "K6015B"]:
                upsert_product(
                    conn,
                    {
                        "bld_no": bld_no,
                        "series": "HYUNDAI",
                        "item": "CONTROL ARM",
                        "oe_no_1": f"OE-{bld_no}",
                        "models": "Sportage",
                        "active": "1",
                    },
                    actor="tester",
                )

        self.login()
        response = self.client.get("/", query_string={"quick_oe": "6004"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("快速号码查询", html)
        self.assertIn("K6004LB", html)
        self.assertIn("K6004RB", html)
        self.assertNotIn("K6015B", html)
        self.assertIn("BLD NO. 片段命中", html)

    def test_quick_partial_number_lookup_checks_bld_oe_and_brand_codes(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            for bld_no, oe_no, brand_no in [
                ("K-DV613-L", "DV613A424AF", "X15CJ6600"),
                ("K-DV613-R", "DV613A423AF", "X15CJ6601"),
                ("K-NUM-5450", "54500-2D000", "BRAND-54500"),
            ]:
                upsert_product(
                    conn,
                    {
                        "bld_no": bld_no,
                        "series": "FORD",
                        "item": "CONTROL ARM",
                        "oe_no_1": oe_no,
                        "oe_no_2": brand_no,
                        "models": "Transit",
                        "active": "1",
                    },
                    actor="tester",
                )

        self.login()
        response = self.client.get("/", query_string={"quick_oe": "dv613"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("K-DV613-L", html)
        self.assertIn("K-DV613-R", html)
        self.assertIn("OE 前缀命中", html)

        response = self.client.get("/", query_string={"quick_oe": "5450"})
        html = response.get_data(as_text=True)
        self.assertIn("K-NUM-5450", html)
        self.assertIn("OE 前缀命中", html)

        response = self.client.get("/", query_string={"quick_oe": "15CJ"})
        html = response.get_data(as_text=True)
        self.assertIn("K-DV613-L", html)
        self.assertIn("K-DV613-R", html)
        self.assertIn("品牌号码片段命中", html)

    def test_quick_lookup_requires_at_least_four_normalized_chars(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-SHORT-001",
                    "series": "FORD",
                    "item": "CONTROL ARM",
                    "oe_no_1": "ABC12345",
                    "models": "Transit",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/", query_string={"quick_oe": "abc"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("请输入至少 4 位号码", html)
        self.assertNotIn("K-SHORT-001", html)

    def test_quick_lookup_uses_unique_oe_suffix_variant(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K8041LB",
                    "series": "VW",
                    "item": "Front Left Lower Control Arm",
                    "oe_no_1": "561407151A\n561407151C",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/", query_string={"quick_oe": "561407151D"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("K8041LB", html)
        self.assertIn("OE 尾字母容错命中", html)

    def test_quick_lookup_psa_352x_dot_prefers_psa_over_gm_exact(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-PSA-352088-QUICK",
                    "series": "PEUGEOT\nCITROEN",
                    "item": "Front Left Lower Control Arm",
                    "oe_no_1": "3520.88",
                    "active": "1",
                },
                actor="tester",
            )
            upsert_product(
                conn,
                {
                    "bld_no": "K-GM-352088-QUICK",
                    "series": "GM\nOPEL",
                    "item": "Front Left Lower Control Arm",
                    "oe_no_1": "352088",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/", query_string={"quick_oe": "3520.88"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("K-PSA-352088-QUICK", html)
        self.assertIn("PSA 号码点号容错命中", html)
        self.assertNotIn("K-GM-352088-QUICK", html)

    def test_single_pasted_code_keeps_quick_lookup(self):
        self.login()
        response = self.client.post("/match", data={"quick_oe": "55270-2Z000"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/?quick_oe=55270-2Z000"))

    def test_pasted_inquiry_has_character_limit(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-LIMIT-001",
                    "oe_no_1": "LIMIT-001",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.post(
            "/match",
            data={"quick_oe": "A" * 5001},
            follow_redirects=True,
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("粘贴号码最多支持 5000 个字符", html)
