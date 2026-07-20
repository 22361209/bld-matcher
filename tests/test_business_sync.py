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
            connection.execute("UPDATE products SET image_path = 'target-image.jpg' WHERE bld_no = 'SYNC-PRODUCT'")
            connection.execute("UPDATE quote_records SET attachment_path = 'target-quote.pdf' WHERE sync_id = 'quote-sync-id'")
            connection.commit()

        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(package)
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        self.assertEqual(cast(dict[str, int], summary["products"]["counts"])["conflict"], 1)
        self.assertEqual(cast(dict[str, int], summary["quotes"]["counts"])["conflict"], 1)
        self.assertEqual(cast(list[dict[str, object]], summary["products"]["conflicts"])[0]["label"], "SYNC-PRODUCT")
        self.assertEqual(cast(list[dict[str, object]], summary["quotes"]["conflicts"])[0]["label"], "同步客户 · SYNC-PRODUCT · — · 2026-07-17")
        result = service.apply(
            package,
            backup_path=self.root / "backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
            selected_conflicts={"products": {"SYNC-PRODUCT"}, "quotes": {"quote-sync-id"}},
        )
        self.assertEqual(result["products"]["updated"], 1)
        self.assertEqual(result["quotes"]["updated"], 1)
        with connect(self.target) as connection:
            product = connection.execute("SELECT updated_at, image_path FROM products WHERE bld_no = 'SYNC-PRODUCT'").fetchone()
            quote = connection.execute("SELECT remark, attachment_path FROM quote_records WHERE sync_id = 'quote-sync-id'").fetchone()
        self.assertEqual(tuple(product), ("2026-07-17 10:00:00", "target-image.jpg"))
        self.assertEqual(tuple(quote), ("source", "target-quote.pdf"))

    def test_export_omits_local_media_paths(self) -> None:
        with connect(self.source) as connection:
            self._seed(connection)
            connection.execute("UPDATE products SET image_path = 'source-image.jpg', drawing_path = 'source-drawing.pdf'")
            connection.execute("UPDATE quote_records SET attachment_path = 'source-quote.pdf'")
            connection.commit()
        package = self.root / "business.tar.gz"
        repository = BusinessSyncRepository(self.source)
        repository.export(output_path=package, selected=("products", "quotes"), actor="test")
        _manifest, payload = repository.read(package)
        self.assertEqual(payload["products"][0]["image_path"], "")
        self.assertEqual(payload["products"][0]["drawing_path"], "")
        self.assertEqual(payload["quotes"][0]["attachment_path"], "")

    def test_selected_material_conflict_overwrites_matching_current_record(self) -> None:
        package = self._package()
        with connect(self.target) as connection:
            self._seed(connection)
            connection.execute("UPDATE material_items SET sync_id = 'target-material-id', pieces = 9")
            connection.commit()

        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(package)
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        conflict = cast(list[dict[str, object]], summary["materials"]["conflicts"])[0]
        self.assertEqual(conflict["label"], "SYNC-MODEL · SYNC-PART · — · — · — · —")
        self.assertEqual(cast(list[dict[str, str]], conflict["fields"])[0], {"label": "下料只数", "before": "9.0", "after": "1.0"})

        result = service.apply(
            package,
            backup_path=self.root / "backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
            selected_conflicts={"materials": {"material-sync-id"}},
        )
        self.assertEqual(result["materials"]["updated"], 1)
        with connect(self.target) as connection:
            material = connection.execute("SELECT sync_id, pieces FROM material_items").fetchone()
        self.assertEqual(tuple(material), ("material-sync-id", 1))

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

    def test_conflict_includes_all_fields_with_changed_flags(self) -> None:
        package = self._package()
        with connect(self.target) as connection:
            self._seed(connection)
            connection.execute("UPDATE material_items SET sync_id = 'target-material-id', pieces = 9")
            connection.commit()

        preview = BusinessSyncService(BusinessSyncRepository(self.target)).preview(package)
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        conflict = cast(list[dict[str, object]], summary["materials"]["conflicts"])[0]
        all_fields = cast(list[dict[str, object]], conflict["all_fields"])
        self.assertTrue(all_fields)
        changed_field_labels = {field.get("label") for field in all_fields if field.get("changed")}
        self.assertIn("下料只数", changed_field_labels)
        # updated_at is excluded from comparison fields.
        self.assertNotIn("updated_at", [field.get("label") for field in all_fields])

    def test_preview_rows_are_not_limited_to_thirty(self) -> None:
        source = self.root / "bulk-source.sqlite3"
        with connect(source) as connection:
            for index in range(35):
                connection.execute(
                    "INSERT INTO products (bld_no, created_at, updated_at) VALUES (?, ?, ?)",
                    (f"BULK-{index:03d}", "2026-07-17 10:00:00", "2026-07-17 10:00:00"),
                )
            connection.commit()
        package = self.root / "bulk-business.tar.gz"
        BusinessSyncService(BusinessSyncRepository(source)).export(
            output_path=package,
            selected=("products",),
            actor="test",
        )

        preview = BusinessSyncService(BusinessSyncRepository(self.target)).preview(package)
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        rows = cast(list[dict[str, object]], summary["products"]["rows"])
        self.assertEqual(len(rows), 35)
