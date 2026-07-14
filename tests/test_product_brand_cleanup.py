from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import connect
from app.modules.products.brand_normalization import BrandNormalizationPreviewChangedError
from app.modules.products.persistence import upsert_product
from app.modules.products.repository import SQLiteProductUnitOfWork
from app.modules.products.service import ProductService
from app.modules.products.sync_repository import SQLiteProductSyncRepository


def _insert_raw_product(
    database_path: Path,
    *,
    bld_no: str,
    series: str,
    source: str = "fixture",
    updated_at: str = "2026-07-14 10:00:00",
) -> None:
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO products (
              bld_no, series, item, active, source, created_at, updated_at
            ) VALUES (?, ?, 'Test Product', 1, ?, ?, ?)
            """,
            (bld_no, series, source, updated_at, updated_at),
        )
        connection.commit()


class ProductBrandCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "data" / "products.sqlite3"
        self.service = ProductService(
            lambda: SQLiteProductUnitOfWork(self.database_path),
            lambda: None,
            lambda: {},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_all_product_writes_canonicalize_brands(self) -> None:
        with connect(self.database_path) as connection:
            upsert_product(
                connection,
                {
                    "bld_no": "BRAND-WRITE-1",
                    "series": "Volkswagen\nRAM\nDODGE\nGREAT WALL",
                    "active": "1",
                },
                actor="tester",
            )
            row = connection.execute(
                "SELECT series FROM products WHERE bld_no = 'BRAND-WRITE-1'"
            ).fetchone()

        self.assertEqual(row["series"], "VOLKSWAGEN\nDODGE\nGREAT WALL")

    def test_service_previews_backs_up_and_applies_one_transaction(self) -> None:
        _insert_raw_product(
            self.database_path,
            bld_no="BRAND-CLEAN-1",
            series="Volkswagen",
            source="manual-history",
        )
        _insert_raw_product(
            self.database_path,
            bld_no="BRAND-CLEAN-2",
            series="DODGE RAM",
            source="catalog-history",
        )
        _insert_raw_product(
            self.database_path,
            bld_no="BRAND-CLEAN-3",
            series="GREAT WALL",
        )

        preview = self.service.preview_brand_normalization()
        self.assertEqual(
            [(change.bld_no, change.before, change.after) for change in preview.changes],
            [
                ("BRAND-CLEAN-1", "Volkswagen", "VOLKSWAGEN"),
                ("BRAND-CLEAN-2", "DODGE RAM", "DODGE"),
            ],
        )

        backup_path = self.root / "data" / "local-backups" / "before-brand-cleanup.sqlite3"
        result = self.service.normalize_brands(
            backup_path=backup_path,
            expected_digest=preview.digest,
            actor="007",
        )
        self.assertEqual(result.changed_count, 2)
        self.assertTrue(backup_path.is_file())

        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT bld_no, series, source FROM products ORDER BY bld_no"
            ).fetchall()
            audit_rows = connection.execute(
                """
                SELECT action, target_key, actor FROM audit_logs
                WHERE action IN ('清洗产品品牌', '批量清洗产品品牌')
                ORDER BY id
                """
            ).fetchall()
        self.assertEqual(
            [(row["bld_no"], row["series"], row["source"]) for row in rows],
            [
                ("BRAND-CLEAN-1", "VOLKSWAGEN", "manual-history"),
                ("BRAND-CLEAN-2", "DODGE", "catalog-history"),
                ("BRAND-CLEAN-3", "GREAT WALL", "fixture"),
            ],
        )
        self.assertEqual(len(audit_rows), 3)
        self.assertTrue(all(row["actor"] == "007" for row in audit_rows))

        with sqlite3.connect(backup_path) as backup:
            backup.row_factory = sqlite3.Row
            original = backup.execute(
                "SELECT series FROM products WHERE bld_no = 'BRAND-CLEAN-2'"
            ).fetchone()
            integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(original["series"], "DODGE RAM")
        self.assertEqual(integrity, "ok")
        self.assertEqual(
            list(backup_path.parent.glob(f".{backup_path.name}.*.tmp")),
            [],
        )
        cleaned_preview = self.service.preview_brand_normalization()
        self.assertEqual(cleaned_preview.changes, ())

        stale_backup = self.root / "data" / "local-backups" / "stale-preview.sqlite3"
        with self.assertRaises(BrandNormalizationPreviewChangedError):
            self.service.normalize_brands(
                backup_path=stale_backup,
                expected_digest=preview.digest,
                actor="007",
            )
        self.assertFalse(stale_backup.exists())

    def test_backup_is_taken_after_write_lock_and_includes_latest_unrelated_change(self) -> None:
        _insert_raw_product(
            self.database_path,
            bld_no="BRAND-CONCURRENT-1",
            series="Volkswagen",
        )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE products SET item = 'OLD' WHERE bld_no = 'BRAND-CONCURRENT-1'"
            )

        database_path = self.database_path

        class ConcurrentBeforeLockUnitOfWork(SQLiteProductUnitOfWork):
            def __enter__(self):
                result = super().__enter__()
                original_lock = self.repository.lock_brand_normalization

                def lock_after_concurrent_update() -> None:
                    with sqlite3.connect(database_path) as concurrent:
                        concurrent.execute(
                            "UPDATE products SET item = 'CONCURRENT' "
                            "WHERE bld_no = 'BRAND-CONCURRENT-1'"
                        )
                    original_lock()

                self.repository.lock_brand_normalization = lock_after_concurrent_update
                return result

        service = ProductService(
            lambda: ConcurrentBeforeLockUnitOfWork(database_path),
            lambda: None,
            lambda: {},
        )
        preview = service.preview_brand_normalization()
        backup_path = self.root / "data" / "local-backups" / "concurrent.sqlite3"
        service.normalize_brands(
            backup_path=backup_path,
            expected_digest=preview.digest,
            actor="007",
        )

        with sqlite3.connect(self.database_path) as live:
            live_item = live.execute(
                "SELECT item FROM products WHERE bld_no = 'BRAND-CONCURRENT-1'"
            ).fetchone()[0]
        with sqlite3.connect(backup_path) as backup:
            backup_item = backup.execute(
                "SELECT item FROM products WHERE bld_no = 'BRAND-CONCURRENT-1'"
            ).fetchone()[0]
        self.assertEqual(live_item, "CONCURRENT")
        self.assertEqual(backup_item, "CONCURRENT")

    def test_backup_failure_removes_temporary_and_final_files(self) -> None:
        _insert_raw_product(
            self.database_path,
            bld_no="BRAND-BACKUP-FAILURE",
            series="Volkswagen",
        )
        backup_path = self.root / "data" / "local-backups" / "failure.sqlite3"
        with SQLiteProductUnitOfWork(self.database_path) as unit_of_work:
            with patch.object(
                Path,
                "replace",
                side_effect=OSError("forced atomic replace failure"),
            ):
                with self.assertRaises(OSError):
                    unit_of_work.repository.backup_database(backup_path)
        self.assertFalse(backup_path.exists())
        self.assertEqual(
            list(backup_path.parent.glob(f".{backup_path.name}.*.tmp")),
            [],
        )

    def test_product_data_sync_normalizes_preview_and_apply(self) -> None:
        package_database = self.root / "incoming.sqlite3"
        _insert_raw_product(
            package_database,
            bld_no="BRAND-SYNC-1",
            series="DODGE RAM\nVolvo",
            source="remote",
            updated_at="2099-07-14 10:00:00",
        )
        repository = SQLiteProductSyncRepository(self.database_path)

        diff = repository.diff(package_database)
        self.assertEqual(diff.new_count, 1)
        result = repository.apply(
            package_database,
            deactivate_local_only=False,
            actor="sync-admin",
        )
        self.assertEqual(result.new_count, 1)

        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT series FROM products WHERE bld_no = 'BRAND-SYNC-1'"
            ).fetchone()
        self.assertEqual(row["series"], "DODGE\nVOLVO")


if __name__ == "__main__":
    unittest.main()
