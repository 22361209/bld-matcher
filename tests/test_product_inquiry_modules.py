from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from app.database import connect
from app.modules.products.persistence import upsert_product
import app.modules.products.persistence as product_persistence
from app.migrations import run_migrations
from app.modules.inquiry.domain import InquiryValidationError
from app.modules.inquiry.infrastructure import WorkbookInquiryEngine
from app.modules.inquiry.repository import SQLiteInquiryUnitOfWork
from app.modules.inquiry.service import InquiryService
from app.modules.products.repository import SQLiteProductUnitOfWork
from app.modules.products.service import ProductService
from app.platform.artifacts import ArtifactNotFoundError, SQLiteArtifactStore


class ProductInquiryModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "data" / "products.sqlite3"
        self.upload_dir = self.root / "uploads"
        self.output_dir = self.root / "outputs"
        self.product_service = ProductService(
            lambda: SQLiteProductUnitOfWork(self.database_path),
            lambda: None,
            lambda: {},
        )
        self.artifact_store = SQLiteArtifactStore(self.database_path, (self.output_dir,))
        self.inquiry_service = InquiryService(
            self.product_service,
            WorkbookInquiryEngine(
                base_dir=self.root,
                upload_dir=self.upload_dir,
                output_dir=self.output_dir,
            ),
            lambda: SQLiteInquiryUnitOfWork(self.database_path),
            self.artifact_store,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_product(self, bld_no: str, oe_no: str, *, item: str = "Test Arm") -> None:
        with connect(self.database_path) as connection:
            upsert_product(
                connection,
                {
                    "bld_no": bld_no,
                    "oe_no_1": oe_no,
                    "item": item,
                    "price_cny": "55",
                    "active": "1",
                },
                actor="module-test",
            )

    def test_product_repository_search_and_catalog_observe_wal_updates(self) -> None:
        self.add_product("K-MODULE-001", "MODULE-OE-001")
        page = self.product_service.search({"oe": "MODULE-OE-001"}, limit=10)
        self.assertEqual(page.total, 1)
        self.assertEqual(page.records[0].bld_no, "K-MODULE-001")
        first_catalog = self.product_service.catalog()
        self.assertIsNotNone(first_catalog)
        self.assertIsNotNone(first_catalog.match("", "MODULE-OE-001"))

        with connect(self.database_path) as connection:
            upsert_product(
                connection,
                {
                    "bld_no": "K-MODULE-001",
                    "oe_no_1": "MODULE-OE-002",
                    "item": "Updated in the same second",
                    "active": "1",
                },
                actor="module-test",
            )
        refreshed = self.product_service.catalog()
        self.assertIsNotNone(refreshed)
        self.assertIsNone(refreshed.match("", "MODULE-OE-001"))
        self.assertEqual(refreshed.match("", "MODULE-OE-002").bld_no, "K-MODULE-001")

    def test_inquiry_service_is_shared_and_artifacts_are_owner_scoped(self) -> None:
        self.add_product("K-MODULE-API", "MODULE-API-OE")
        analysis = self.inquiry_service.run_numbers(
            {"numbers": ["MODULE-API-OE"], "price_mode": "net"},
            export=False,
            actor="consumer-a",
        )
        self.assertEqual(analysis.summary["matched"], 1)
        self.assertEqual(analysis.api_payload()["rows"][0]["bld_no"], "K-MODULE-API")
        self.assertEqual(
            self.inquiry_service.quick_search("MODULE-API-OE")[0]["product"]["bld_no"],
            "K-MODULE-API",
        )

        exported = self.inquiry_service.run_numbers(
            {"numbers": ["MODULE-API-OE"], "source_name": "module-contract"},
            export=True,
            actor="consumer-a",
            artifact_owner="key:101",
        )
        self.assertIsNotNone(exported.artifact)
        artifact = self.artifact_store.get(exported.artifact.id, owner_id="key:101")
        self.assertTrue(artifact.storage_path.is_file())
        with self.assertRaises(ArtifactNotFoundError):
            self.artifact_store.get(exported.artifact.id, owner_id="key:202")

        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE api_artifacts SET expires_at = '2000-01-01 00:00:00' WHERE id = ?",
                (artifact.id,),
            )
            connection.commit()
        with self.assertRaises(ArtifactNotFoundError):
            self.artifact_store.get(artifact.id, owner_id="key:101")
        self.assertEqual(self.artifact_store.purge_expired(), 1)

    def test_inquiry_validation_and_alias_transaction(self) -> None:
        self.add_product("K-MODULE-ALIAS", "MODULE-PRIMARY")
        with self.assertRaises(InquiryValidationError) as context:
            self.inquiry_service.run_numbers({}, export=False, actor="module-test")
        self.assertEqual(context.exception.code, "inquiry.numbers_required")

        appended = self.inquiry_service.save_alias(
            "MODULE-CUSTOMER",
            "K-MODULE-ALIAS",
            "consumer mapping",
            "oe",
            actor="module-test",
        )
        self.assertTrue(appended)
        result = self.inquiry_service.run_numbers(
            {"numbers": ["MODULE-CUSTOMER"]},
            export=False,
            actor="module-test",
        )
        self.assertEqual(result.summary["rows"][0]["bld_no"], "K-MODULE-ALIAS")
        with connect(self.database_path) as connection:
            alias = connection.execute(
                "SELECT bld_no, active FROM aliases WHERE source_code = ?",
                ("MODULECUSTOMER",),
            ).fetchone()
            audit_actions = {
                row["action"] for row in connection.execute("SELECT action FROM audit_logs").fetchall()
            }
        self.assertEqual(alias["bld_no"], "K-MODULE-ALIAS")
        self.assertEqual(alias["active"], 1)
        self.assertIn("新增人工映射", audit_actions)
        self.assertIn("追加OE 号", audit_actions)

    def test_historical_database_adds_artifact_table(self) -> None:
        with connect(self.database_path) as connection:
            connection.execute("DROP TABLE api_artifacts")
            connection.execute("DELETE FROM schema_migrations WHERE id = '016_api_artifacts'")
            connection.commit()
        raw = sqlite3.connect(self.database_path)
        raw.row_factory = sqlite3.Row
        try:
            run_migrations(raw)
            columns = {
                row["name"] for row in raw.execute("PRAGMA table_info(api_artifacts)").fetchall()
            }
            migration = raw.execute(
                "SELECT id FROM schema_migrations WHERE id = '016_api_artifacts'"
            ).fetchone()
        finally:
            raw.close()
        self.assertIn("storage_path", columns)
        self.assertIn("expires_at", columns)
        self.assertIsNotNone(migration)

    def test_catalog_import_rolls_back_the_whole_batch(self) -> None:
        catalog_path = self.root / "catalog.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["BLD NO.", "OE NO.1", "ITEM"])
        sheet.append(["K-IMPORT-001", "IMPORT-OE-001", "First"])
        sheet.append(["K-IMPORT-002", "IMPORT-OE-002", "Second"])
        workbook.save(catalog_path)
        workbook.close()

        original_upsert = product_persistence.upsert_product
        calls = 0

        def fail_on_second(connection, data, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated batch failure")
            return original_upsert(connection, data, *args, **kwargs)

        with patch.object(product_persistence, "upsert_product", side_effect=fail_on_second):
            with self.assertRaises(RuntimeError):
                self.product_service.import_catalog(catalog_path, actor="module-test")

        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT bld_no FROM products WHERE bld_no LIKE 'K-IMPORT-%'"
            ).fetchall()
            logs = connection.execute(
                "SELECT id FROM audit_logs WHERE target_key = ?",
                (catalog_path.name,),
            ).fetchall()
        self.assertEqual(rows, [])
        self.assertEqual(logs, [])


if __name__ == "__main__":
    unittest.main()
