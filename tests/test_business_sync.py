from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import cast

from openpyxl import Workbook

from app.database import connect
from app.modules.business_sync.infrastructure import BusinessSyncRepository
from app.modules.business_sync.service import BusinessSyncService
from app.modules.materials.excel_import import import_materials_from_excel


class BusinessSyncServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.sqlite3"
        self.target = self.root / "target.sqlite3"
        with connect(self.source), connect(self.target):
            pass

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _seed(connection, *, updated_at: str = "2026-07-17 10:00:00", quote_remark: str = "source") -> None:
        connection.execute(
            "INSERT INTO products (bld_no, created_at, updated_at) VALUES (?, ?, ?)",
            ("SYNC-PRODUCT", updated_at, updated_at),
        )
        connection.execute(
            """
            INSERT INTO quote_records
              (sync_id, customer_name, bld_no, product_model, price, currency, quote_date, remark, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("quote-sync-id", "同步客户", "SYNC-PRODUCT", "SYNC-PRODUCT", 10, "USD", "2026-07-17", quote_remark, updated_at, updated_at),
        )
        connection.execute(
            "INSERT INTO tube_items (code, created_at, updated_at) VALUES (?, ?, ?)",
            ("SYNC-TUBE", updated_at, updated_at),
        )
        connection.execute(
            """
            INSERT INTO material_items
              (sync_id, model, code, pieces, thickness, width, length, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("material-sync-id", "SYNC-MODEL", "SYNC-PART", 1, 2, 3, 4, updated_at, updated_at),
        )

    def _package(self) -> Path:
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()
        package = self.root / "business.tar.gz"
        BusinessSyncService(BusinessSyncRepository(self.source)).export(
            output_path=package,
            selected=("products", "quotes", "tubes", "materials"),
            actor="test",
        )
        return package

    def _write_package(self, payload: dict[str, list[dict[str, object]]]) -> Path:
        package = self.root / "custom-business.tar.gz"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(
                json.dumps({"package_type": "bld_business_data", "version": 1, "datasets": list(payload)}),
                encoding="utf-8",
            )
            (root / "data.json").write_text(json.dumps(payload), encoding="utf-8")
            with tarfile.open(package, "w:gz") as archive:
                archive.add(root / "manifest.json", arcname="manifest.json")
                archive.add(root / "data.json", arcname="data.json")
        return package

    def test_export_preview_and_apply_round_trip_all_datasets(self) -> None:
        package = self._package()
        target_service = BusinessSyncService(BusinessSyncRepository(self.target))

        preview = target_service.preview(package)
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        self.assertEqual(
            {key: cast(dict[str, int], info["counts"])["new"] for key, info in summary.items()},
            {"products": 1, "quotes": 1, "tubes": 1, "materials": 1},
        )

        result = target_service.apply(
            package,
            backup_path=self.root / "backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
        )
        self.assertEqual({key: counts["new"] for key, counts in result.items()}, {"products": 1, "quotes": 1, "tubes": 1, "materials": 1})
        self.assertTrue((self.root / "backup.sqlite3").is_file())
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM products WHERE bld_no = 'SYNC-PRODUCT'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM quote_records WHERE sync_id = 'quote-sync-id'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tube_items WHERE code = 'SYNC-TUBE'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM material_items WHERE sync_id = 'material-sync-id'").fetchone()[0], 1)

    def test_older_product_and_different_quote_are_reported_as_conflicts(self) -> None:
        package = self._package()
        with connect(self.target) as connection:
            self._seed(connection, updated_at="2026-07-18 10:00:00", quote_remark="target")
            connection.commit()

        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(package)
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        self.assertEqual(cast(dict[str, int], summary["products"]["counts"])["conflict"], 1)
        self.assertEqual(cast(dict[str, int], summary["quotes"]["counts"])["conflict"], 1)
        result = service.apply(
            package,
            backup_path=self.root / "backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
        )
        self.assertEqual(result["products"]["conflict"], 1)
        self.assertEqual(result["quotes"]["conflict"], 1)
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT updated_at FROM products WHERE bld_no = 'SYNC-PRODUCT'").fetchone()[0], "2026-07-18 10:00:00")
            self.assertEqual(connection.execute("SELECT remark FROM quote_records WHERE sync_id = 'quote-sync-id'").fetchone()[0], "target")

    def test_first_sync_adopts_matching_quote_and_material_identity(self) -> None:
        package = self._package()
        with connect(self.target) as connection:
            self._seed(connection)
            connection.execute("UPDATE quote_records SET sync_id = 'target-quote-id'")
            connection.execute("UPDATE material_items SET sync_id = 'target-material-id'")
            connection.commit()

        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(package)
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        self.assertEqual(cast(dict[str, int], summary["quotes"]["counts"])["updated"], 1)
        self.assertEqual(cast(dict[str, int], summary["materials"]["counts"])["updated"], 1)
        service.apply(
            package,
            backup_path=self.root / "backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
        )
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT sync_id FROM quote_records").fetchone()[0], "quote-sync-id")
            self.assertEqual(connection.execute("SELECT sync_id FROM material_items").fetchone()[0], "material-sync-id")

    def test_duplicate_identity_and_stale_preview_are_rejected_without_writes(self) -> None:
        package = self._write_package(
            {
                "products": [
                    {"bld_no": "DUP-001", "created_at": "2026-07-17 10:00:00", "updated_at": "2026-07-17 10:00:00"},
                    {"bld_no": "DUP-001", "created_at": "2026-07-17 10:00:00", "updated_at": "2026-07-17 10:00:00"},
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "重复编号"):
            BusinessSyncService(BusinessSyncRepository(self.target)).preview(package)

        package = self._package()
        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(package)
        with connect(self.target) as connection:
            connection.execute(
                "INSERT INTO products (bld_no, created_at, updated_at) VALUES ('AFTER-PREVIEW', '2026-07-18 10:00:00', '2026-07-18 10:00:00')"
            )
            connection.commit()
        with self.assertRaisesRegex(ValueError, "重新上传预览"):
            service.apply(
                package,
                backup_path=self.root / "backup.sqlite3",
                actor="test",
                expected_token=cast(str, preview["token"]),
            )
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM products WHERE bld_no = 'SYNC-PRODUCT'").fetchone()[0], 0)

    def test_material_excel_renames_keep_sync_identity(self) -> None:
        def workbook(path: Path) -> None:
            book = Workbook()
            sheet = book.active
            assert sheet is not None
            sheet.title = "材料数据"
            sheet.append(["母件", "零件", "类别", "车型", "名称", "", "只数", "", "厚", "宽", "长"])
            sheet.append(["MAT-001", "PART-001", "类别", "车型", "零件", "", 2, "", 3, 40, 120])
            book.save(path)
            book.close()

        first = self.root / "materials-a.xlsx"
        second = self.root / "materials-renamed.xlsx"
        workbook(first)
        workbook(second)
        first_db = self.root / "first.sqlite3"
        second_db = self.root / "second.sqlite3"
        with connect(first_db) as connection:
            import_materials_from_excel(connection, first, replace=True, actor="test")
            first_id = connection.execute("SELECT sync_id FROM material_items").fetchone()[0]
        with connect(second_db) as connection:
            import_materials_from_excel(connection, second, replace=True, actor="test")
            second_id = connection.execute("SELECT sync_id FROM material_items").fetchone()[0]
        self.assertEqual(first_id, second_id)
