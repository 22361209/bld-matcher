from __future__ import annotations

from tests.web_app_test_base import WebAppTestBase, patch


class ProductStatusOptionsTest(WebAppTestBase):
    def _status_rows(self):
        with self.web.connect(self.web.DB_PATH) as connection:
            return connection.execute(
                "SELECT id, value FROM product_option_values WHERE kind = 'product_status'"
            ).fetchall()

    def _save_status(self, value: str, option_id: int | None = None):
        data = {"kind": "product_status", "value": value}
        if option_id is not None:
            data["id"] = str(option_id)
        return self.client.post("/product-options/save", data=data, follow_redirects=True)

    def test_chinese_wording_rename_persists_without_changing_existing_products(self):
        self.login()
        original = "41球头42衬套"
        edited = "41 个球头 42 个衬套"
        product = {
            "bld_no": "STATUS-RENAME-01",
            "item": "Status test",
            "product_status": original,
            "active": "1",
        }
        saved = self.client.post("/products/save", data=product)
        self.assertEqual(saved.status_code, 302)
        option_id = next(row["id"] for row in self._status_rows() if row["value"] == original)

        response = self._save_status(edited, option_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("候选值已保存", response.get_data(as_text=True))
        self.assertIn(f'value="{edited}"', response.get_data(as_text=True))
        self.assertEqual(next(row["value"] for row in self._status_rows() if row["id"] == option_id), edited)
        self.assertIn(edited, self.client.get("/products/options").get_json()["statuses"])

        # Automatic registration on a subsequent product save must retain the edited label.
        self.client.post("/products/save", data=product)
        from app.product_status import canonical_product_status, format_product_status

        equivalent = [row["value"] for row in self._status_rows() if canonical_product_status(row["value"]) == original]
        self.assertEqual(equivalent, [edited])
        self.assertEqual(format_product_status(edited, "en", multiline=False), "41 ball joints 42 bushings")
        with self.web.connect(self.web.DB_PATH) as connection:
            status = connection.execute(
                "SELECT product_status FROM products WHERE bld_no = ?", (product["bld_no"],)
            ).fetchone()[0]
            audit = connection.execute(
                "SELECT detail FROM audit_logs WHERE action = '改名产品候选值' AND target_key = ?",
                (f"product_status:{edited}",),
            ).fetchone()
        self.assertEqual(status, original)
        self.assertEqual(audit[0], f"{original} -> {edited}")
        filtered = self.client.get("/products", query_string={"product_status": edited})
        self.assertIn(product["bld_no"], filtered.get_data(as_text=True))

    def test_new_chinese_status_preserves_wording_and_rejects_equivalent_duplicates(self):
        self.login()
        label = "51 个衬套 52 个球头"
        self._save_status(label)
        self.assertIn(label, [row["value"] for row in self._status_rows()])
        duplicate = self._save_status("051衬套52球头")
        self.assertIn("已存在", duplicate.get_data(as_text=True))

        other = "状态查重测试"
        self._save_status(other)
        other_id = next(row["id"] for row in self._status_rows() if row["value"] == other)
        rename = self._save_status("51衬套52个球头", other_id)
        self.assertIn("已存在", rename.get_data(as_text=True))
        self.assertEqual(next(row["value"] for row in self._status_rows() if row["id"] == other_id), other)

    def test_freeform_chinese_rename_and_audit_failure_rollback(self):
        self.login()
        original, edited = "测试状态待开发", "测试状态已量产（待确认包装）"
        self._save_status(original)
        option_id = next(row["id"] for row in self._status_rows() if row["value"] == original)
        with patch("app.modules.products.repository.log_event", side_effect=RuntimeError("audit unavailable")):
            failed = self._save_status(edited, option_id)
        self.assertIn("候选值保存失败", failed.get_data(as_text=True))
        self.assertEqual(next(row["value"] for row in self._status_rows() if row["id"] == option_id), original)

        self._save_status(edited, option_id)
        self.assertEqual(next(row["value"] for row in self._status_rows() if row["id"] == option_id), edited)
