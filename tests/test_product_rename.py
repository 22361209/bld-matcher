from __future__ import annotations

import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_web_module():
    spec = spec_from_file_location("bld_matcher_test_web", PROJECT_ROOT / "app.py")
    module = module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["bld_matcher_test_web"] = module
    spec.loader.exec_module(module)
    return module


class ProductRenameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.root = root
        os.environ["SECRET_KEY"] = "test-secret"
        os.environ["MAX_UPLOAD_MB"] = "20"
        os.environ["PRODUCT_SYNC_MAX_UPLOAD_MB"] = "512"
        os.environ["BLD_DATA_DIR"] = str(root / "data")
        os.environ["BLD_UPLOAD_DIR"] = str(root / "uploads")
        os.environ["BLD_OUTPUT_DIR"] = str(root / "outputs")
        os.environ["DEFAULT_ADMIN_PASSWORD"] = "test-admin-pw"
        os.environ["INTERNAL_API_TOKEN"] = ""
        for module_name in [name for name in sys.modules if name == "app" or name.startswith("app.")]:
            sys.modules.pop(module_name, None)
        cls.web = load_web_module()
        if not cls.web.DB_PATH.resolve().is_relative_to(root.resolve()):
            raise RuntimeError(f"Tests must use the isolated database under {root}, got {cls.web.DB_PATH}")
        cls.web.app.config["TESTING"] = True
        cls.client = cls.web.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.client = None
        cls.web = None
        gc.collect()
        cls.tmp.cleanup()

    def login(self, username="007", password="test-admin-pw"):
        return self.client.post(
            "/login",
            data={"username": username, "password": password, "next": "/"},
            follow_redirects=False,
        )

    def _create_editor(self, username="editor-rename"):
        from app.modules.admin.persistence import save_user

        with self.web.connect(self.web.DB_PATH) as conn:
            save_user(
                conn,
                {
                    "username": username,
                    "display_name": "Editor Rename",
                    "password": "editor-pw",
                    "role": "editor",
                    "active": "1",
                },
                actor="tester",
            )

    def _insert_product(self, bld_no: str, image: bool = False, drawing: bool = False):
        from app.modules.products.persistence import upsert_product

        image_dir = self.root / "data" / "product_images"
        image_dir.mkdir(parents=True, exist_ok=True)
        drawing_dir = self.root / "data" / "drawings" / "pdf"
        drawing_dir.mkdir(parents=True, exist_ok=True)

        image_path = ""
        if image:
            image_file = image_dir / f"{bld_no}.png"
            Image.new("RGB", (80, 40), "white").save(image_file)
            image_path = f"data_product_images/{bld_no}.png"

        drawing_path = ""
        if drawing:
            drawing_file = drawing_dir / f"{bld_no}.pdf"
            drawing_file.write_bytes(b"%PDF-1.4 test")
            drawing_path = f"drawings/pdf/{bld_no}.pdf"

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": bld_no,
                    "series": "TEST",
                    "item": "Test Item",
                    "oe_no_1": "OE-1",
                    "models": "MODEL-A",
                    "price_cny": "100.00",
                    "product_status": "量产",
                    "image_path": image_path,
                    "drawing_path": drawing_path,
                },
                actor="tester",
                commit=True,
            )

    def _insert_alias(self, source_code: str, bld_no: str):
        from app.platform.clock import now_text

        with self.web.connect(self.web.DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO aliases (source_code, bld_no, active, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (source_code, bld_no, now_text(), now_text()),
            )
            conn.commit()

    def _insert_quote_record(self, bld_no: str):
        from app.platform.clock import now_text

        with self.web.connect(self.web.DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO quote_records
                  (customer_name, bld_no, product_model, tax_price, net_price, currency,
                   moq, quote_date, quoted_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Customer",
                    bld_no,
                    "MODEL-A",
                    120.0,
                    100.0,
                    "CNY",
                    100,
                    "2026-07-27",
                    "tester",
                    now_text(),
                    now_text(),
                ),
            )
            conn.commit()

    def _insert_customer_price(self, bld_no: str):
        from app.platform.clock import now_text

        with self.web.connect(self.web.DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO customer_price_records
                  (customer_name, record_date, bld_no, price_cny, currency, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("Customer", "2026-07-27", bld_no, 110.0, "CNY", now_text(), now_text()),
            )
            conn.commit()

    def _bld_exists(self, bld_no: str) -> bool:
        with self.web.connect(self.web.DB_PATH) as conn:
            row = conn.execute("SELECT 1 FROM products WHERE bld_no = ?", (bld_no,)).fetchone()
            return row is not None

    def _counts_for(self, old_bld: str, new_bld: str) -> dict[str, int]:
        with self.web.connect(self.web.DB_PATH) as conn:
            return {
                "products": conn.execute(
                    "SELECT COUNT(*) FROM products WHERE bld_no = ?", (new_bld,)
                ).fetchone()[0],
                "old_products": conn.execute(
                    "SELECT COUNT(*) FROM products WHERE bld_no = ?", (old_bld,)
                ).fetchone()[0],
                "aliases": conn.execute(
                    "SELECT COUNT(*) FROM aliases WHERE bld_no = ?", (new_bld,)
                ).fetchone()[0],
                "quote_records": conn.execute(
                    "SELECT COUNT(*) FROM quote_records WHERE bld_no = ?", (new_bld,)
                ).fetchone()[0],
                "customer_price_records": conn.execute(
                    "SELECT COUNT(*) FROM customer_price_records WHERE bld_no = ?", (new_bld,)
                ).fetchone()[0],
            }

    def test_admin_can_rename_bld_no_and_cascade_changes(self):
        self.login()
        self._insert_product("K-RENAME-OLD", image=True, drawing=True)
        self._insert_alias("OLD-CODE", "K-RENAME-OLD")
        self._insert_quote_record("K-RENAME-OLD")
        self._insert_customer_price("K-RENAME-OLD")

        response = self.client.post(
            "/products/K-RENAME-OLD/rename",
            data={"new_bld_no": "K-RENAME-NEW"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/products", response.headers["Location"])

        counts = self._counts_for("K-RENAME-OLD", "K-RENAME-NEW")
        self.assertEqual(counts["products"], 1)
        self.assertEqual(counts["old_products"], 0)
        self.assertEqual(counts["aliases"], 1)
        self.assertEqual(counts["quote_records"], 1)
        self.assertEqual(counts["customer_price_records"], 1)

        image_dir = self.root / "data" / "product_images"
        self.assertFalse((image_dir / "K-RENAME-OLD.png").exists())
        self.assertTrue((image_dir / "K-RENAME-NEW.png").exists())

        drawing_dir = self.root / "data" / "drawings" / "pdf"
        self.assertFalse((drawing_dir / "K-RENAME-OLD.pdf").exists())
        self.assertTrue((drawing_dir / "K-RENAME-NEW.pdf").exists())

        with self.web.connect(self.web.DB_PATH) as conn:
            audit = conn.execute(
                "SELECT * FROM audit_logs WHERE action = ? AND target_key = ?",
                ("产品型号迁移", "K-RENAME-NEW"),
            ).fetchone()
        self.assertIsNotNone(audit)

    def test_editor_cannot_access_rename(self):
        self._create_editor("editor-rename-deny")
        self._insert_product("K-RENAME-EDITOR")
        self.login("editor-rename-deny", "editor-pw")

        response = self.client.get("/products/K-RENAME-EDITOR/rename")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

        response = self.client.post(
            "/products/K-RENAME-EDITOR/rename",
            data={"new_bld_no": "K-RENAME-EDITOR-NEW"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))
        self.assertTrue(self._bld_exists("K-RENAME-EDITOR"))
        self.assertFalse(self._bld_exists("K-RENAME-EDITOR-NEW"))

    def test_rename_fails_when_target_bld_no_exists(self):
        self.login()
        self._insert_product("K-RENAME-SOURCE")
        self._insert_product("K-RENAME-TARGET")

        response = self.client.post(
            "/products/K-RENAME-SOURCE/rename",
            data={"new_bld_no": "K-RENAME-TARGET"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self._bld_exists("K-RENAME-SOURCE"))
        self.assertTrue(self._bld_exists("K-RENAME-TARGET"))

    def test_rename_form_link_visible_to_admin_only(self):
        self.login()
        self._insert_product("K-RENAME-LINK")
        response = self.client.get(f"/products/{self._product_id('K-RENAME-LINK')}/edit")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("迁移型号", body)
        self.assertIn("/products/K-RENAME-LINK/rename", body)

    def test_rename_form_link_hidden_from_editor(self):
        self._create_editor("editor-rename-link")
        self._insert_product("K-RENAME-LINK-EDITOR")
        self.login("editor-rename-link", "editor-pw")
        response = self.client.get(f"/products/{self._product_id('K-RENAME-LINK-EDITOR')}/edit")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn("迁移型号", body)

    def _product_id(self, bld_no: str) -> int:
        with self.web.connect(self.web.DB_PATH) as conn:
            row = conn.execute("SELECT id FROM products WHERE bld_no = ?", (bld_no,)).fetchone()
        self.assertIsNotNone(row)
        return int(row["id"])


if __name__ == "__main__":
    unittest.main()
