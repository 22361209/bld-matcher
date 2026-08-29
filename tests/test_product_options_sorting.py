from __future__ import annotations

from tests.web_app_test_base import WebAppTestBase


class TestProductOptionSorting(WebAppTestBase):
    def _upsert_item(self, conn, bld_no: str, item: str, *, active: str = "1") -> None:
        from app.modules.products.persistence import upsert_product

        upsert_product(
            conn,
            {
                "bld_no": bld_no,
                "item": item,
                "series": "SORTTEST",
                "active": active,
            },
            actor="tester",
        )

    def _seed_frequency_items(self) -> None:
        with self.web.connect(self.web.DB_PATH) as conn:
            for index in range(3):
                self._upsert_item(conn, f"ZS-SORT-H{index}", "ZS-FREQ-HIGH")
            for index in range(2):
                self._upsert_item(conn, f"ZS-SORT-M{index}", "ZS-FREQ-MID")
            self._upsert_item(conn, "ZS-SORT-L0", "ZS-FREQ-LOW")
            self._upsert_item(conn, "ZS-SORT-X0", "ZS-INACTIVE-ONLY", active="0")
            conn.commit()

    def _options(self, kind: str) -> list[str]:
        return [option.value for option in self.web_app_option_values() if option.kind == kind]

    def web_app_option_values(self):
        with self.web.connect(self.web.DB_PATH) as conn:
            from app.modules.products.option_values import list_option_values

            return list_option_values(conn)

    def _seed_custom_items(self) -> dict[str, int]:
        with self.web.connect(self.web.DB_PATH) as conn:
            for value in ("ZS-CUSTOM-1", "ZS-CUSTOM-2", "ZS-CUSTOM-3"):
                conn.execute("INSERT OR IGNORE INTO product_option_values (kind, value) VALUES ('item', ?)", (value,))
            conn.commit()
            rows = conn.execute(
                "SELECT id, value FROM product_option_values WHERE value LIKE 'ZS-CUSTOM-%'"
            ).fetchall()
        return {row["value"] if hasattr(row, "keys") else row[1]: row["id"] if hasattr(row, "keys") else row[0] for row in rows}

    def test_item_options_default_to_usage_frequency(self) -> None:
        self._seed_frequency_items()
        ordered = [value for value in self._options("item") if value.startswith("ZS-")]
        self.assertEqual(
            ordered,
            ["ZS-FREQ-HIGH", "ZS-FREQ-MID", "ZS-FREQ-LOW", "ZS-INACTIVE-ONLY"],
        )
        counts = {
            option.value: option.usage_count
            for option in self.web_app_option_values()
            if option.kind == "item" and option.value.startswith("ZS-")
        }
        self.assertEqual(counts["ZS-FREQ-HIGH"], 3)
        self.assertEqual(counts["ZS-INACTIVE-ONLY"], 0)

    def test_move_persists_custom_order_for_picker_and_audit(self) -> None:
        ids = self._seed_custom_items()
        self.login()

        response = self.client.post(
            "/product-options/move",
            data={"id": ids["ZS-CUSTOM-3"], "direction": "up", "view": "item"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=item", response.headers["Location"])

        picker = self.client.get("/products/options")
        self.assertEqual(picker.status_code, 200)
        custom = [value for value in picker.get_json()["items"] if value.startswith("ZS-CUSTOM-")]
        self.assertEqual(custom, ["ZS-CUSTOM-1", "ZS-CUSTOM-3", "ZS-CUSTOM-2"])

        # 排序边界不报错且顺序不变
        first_id = self._seed_custom_items()["ZS-CUSTOM-1"]
        boundary = self.client.post(
            "/product-options/move",
            data={"id": first_id, "direction": "up", "view": "item"},
        )
        self.assertEqual(boundary.status_code, 302)
        picker = self.client.get("/products/options")
        custom = [value for value in picker.get_json()["items"] if value.startswith("ZS-CUSTOM-")]
        self.assertEqual(custom, ["ZS-CUSTOM-1", "ZS-CUSTOM-3", "ZS-CUSTOM-2"])

        with self.web.connect(self.web.DB_PATH) as conn:
            audit = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE action = '调整产品候选值顺序'"
            ).fetchone()[0]
        self.assertGreaterEqual(audit, 1)

    def test_new_option_lands_after_customized_order(self) -> None:
        ids = self._seed_custom_items()
        self.login()
        self.client.post(
            "/product-options/move",
            data={"id": ids["ZS-CUSTOM-3"], "direction": "up", "view": "item"},
        )
        self.client.post(
            "/product-options/save",
            data={"kind": "item", "value": "ZS-CUSTOM-0-LATE"},
        )
        picker = self.client.get("/products/options")
        custom = [value for value in picker.get_json()["items"] if value.startswith("ZS-CUSTOM-")]
        self.assertEqual(custom, ["ZS-CUSTOM-1", "ZS-CUSTOM-3", "ZS-CUSTOM-2", "ZS-CUSTOM-0-LATE"])

    def test_options_page_defaults_to_item_card_with_tabs(self) -> None:
        self._seed_frequency_items()
        self.login()

        default = self.client.get("/product-options")
        default_html = default.get_data(as_text=True)
        self.assertEqual(default.status_code, 200)
        self.assertIn("<h2>产品名称</h2>", default_html)
        self.assertNotIn("<h2>品牌</h2>", default_html)
        self.assertIn("使用次数", default_html)
        self.assertIn('aria-current="page">产品名称', default_html)

        brand = self.client.get("/product-options?view=brand")
        brand_html = brand.get_data(as_text=True)
        self.assertIn("<h2>品牌</h2>", brand_html)
        self.assertNotIn("<h2>产品名称</h2>", brand_html)
        self.assertNotIn("使用次数", brand_html)

    def test_save_and_delete_redirect_back_to_current_view(self) -> None:
        self.login()
        saved = self.client.post(
            "/product-options/save",
            data={"kind": "brand", "value": "ZS-SORT-BRAND"},
        )
        self.assertEqual(saved.status_code, 302)
        self.assertIn("view=brand", saved.headers["Location"])

        with self.web.connect(self.web.DB_PATH) as conn:
            row = conn.execute(
                "SELECT id FROM product_option_values WHERE kind = 'brand' AND value = 'ZS-SORT-BRAND'"
            ).fetchone()
        deleted = self.client.post(
            "/product-options/delete",
            data={"id": row[0], "view": "brand"},
        )
        self.assertEqual(deleted.status_code, 302)
        self.assertIn("view=brand", deleted.headers["Location"])
