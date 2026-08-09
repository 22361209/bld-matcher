from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

from openpyxl import Workbook

from app.database import connect
from app.modules.business_sync import _database_apply as database_apply
from app.modules.business_sync import _media_transaction as media_transaction
from app.modules.business_sync import _package_archive as package_archive
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
            "INSERT OR IGNORE INTO customers (name, sync_id) VALUES (?, ?)",
            ("同步客户", "customer-sync-id"),
        )
        connection.execute(
            "INSERT INTO products (bld_no, created_at, updated_at) VALUES (?, ?, ?)",
            ("SYNC-PRODUCT", updated_at, updated_at),
        )
        connection.execute(
            """
            INSERT INTO quote_records
              (sync_id, customer_name, bld_no, product_model, tax_price, currency, quote_date, remark, created_at, updated_at)
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

    def _write_raw_package(
        self,
        *,
        name: str,
        manifest: dict[str, object],
        payload: dict[str, list[dict[str, object]]],
        media: dict[str, bytes] | None = None,
    ) -> Path:
        package = self.root / name
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "data.json").write_text(json.dumps(payload), encoding="utf-8")
            with tarfile.open(package, "w:gz") as archive:
                archive.add(root / "manifest.json", arcname="manifest.json")
                archive.add(root / "data.json", arcname="data.json")
                for member_name, content in (media or {}).items():
                    member = tarfile.TarInfo(member_name)
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
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
            customer_mappings={"同步客户": None},
        )
        self.assertEqual({key: counts["new"] for key, counts in result.items()}, {"products": 1, "quotes": 1, "tubes": 1, "materials": 1})
        self.assertTrue((self.root / "backup.sqlite3").is_file())
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM customers WHERE name = '同步客户'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM products WHERE bld_no = 'SYNC-PRODUCT'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM quote_records WHERE sync_id = 'quote-sync-id'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tube_items WHERE code = 'SYNC-TUBE'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM material_items WHERE sync_id = 'material-sync-id'").fetchone()[0], 1)

    def test_customer_owner_is_device_local_and_not_overwritten_by_sync(self) -> None:
        with connect(self.source) as connection:
            connection.execute(
                "INSERT INTO customers (name, code, owner_username, sync_id) VALUES (?, ?, ?, ?)",
                ("负责人同步客户", "SYNC-CUSTOMER", "source-owner", "owner-customer-sync-id"),
            )
            connection.commit()
        with connect(self.target) as connection:
            connection.execute(
                "INSERT INTO customers (name, code, owner_username, sync_id) VALUES (?, ?, ?, ?)",
                ("负责人同步客户", "SYNC-CUSTOMER", "local-owner", "owner-customer-sync-id"),
            )
            connection.commit()

        package = self.root / "customer-owner-business.tar.gz"
        source_repository = BusinessSyncRepository(self.source)
        source_repository.export(output_path=package, selected=("customers",), actor="test")
        _manifest, payload = source_repository.read(package)
        self.assertEqual(payload["customers"][0]["owner_username"], "")

        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(package)
        result = service.apply(
            package,
            backup_path=self.root / "customer-owner-backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
        )

        self.assertEqual(result["customers"]["unchanged"], 1)
        with connect(self.target) as connection:
            owner = connection.execute(
                "SELECT owner_username FROM customers WHERE sync_id = 'owner-customer-sync-id'"
            ).fetchone()[0]
        self.assertEqual(owner, "local-owner")

    def test_customer_rename_keeps_linked_quote_canonical_for_customer_and_combined_packages(self) -> None:
        with connect(self.source) as connection:
            source_customer_id = connection.execute(
                """
                INSERT INTO customers (name, sync_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                ("客户新名称", "renamed-customer-sync-id", "2026-07-17 10:00:00", "2026-07-19 10:00:00"),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO quote_records
                  (customer_id, customer_name, bld_no, product_model, tax_price, currency,
                   quote_date, remark, version, sync_id, quote_no, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_customer_id,
                    "客户新名称",
                    "RENAME-001",
                    "RENAME-001",
                    10,
                    "CNY",
                    "2026-07-19",
                    "source",
                    4,
                    "renamed-quote-sync-id",
                    "Q-RENAME-001",
                    "2026-07-17 10:00:00",
                    "2026-07-19 10:00:00",
                ),
            )
            connection.commit()

        def seed_target(database_path: Path) -> int:
            with connect(database_path) as connection:
                customer_id = connection.execute(
                    """
                    INSERT INTO customers (name, sync_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("客户旧名称", "renamed-customer-sync-id", "2026-07-17 10:00:00", "2026-07-18 10:00:00"),
                ).lastrowid
                connection.execute(
                    """
                    INSERT INTO quote_records
                      (customer_id, customer_name, bld_no, product_model, tax_price, currency,
                       quote_date, remark, version, sync_id, quote_no, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        customer_id,
                        "客户旧名称",
                        "RENAME-001",
                        "RENAME-001",
                        10,
                        "CNY",
                        "2026-07-19",
                        "target",
                        4,
                        "renamed-quote-sync-id",
                        "Q-RENAME-001",
                        "2026-07-17 10:00:00",
                        "2026-07-18 10:00:00",
                    ),
                )
                connection.commit()
                return int(customer_id)

        repository = BusinessSyncRepository(self.source)
        customer_package = self.root / "customer-rename-only.tar.gz"
        combined_package = self.root / "customer-rename-with-quotes.tar.gz"
        repository.export(output_path=customer_package, selected=("customers",), actor="test")
        repository.export(output_path=combined_package, selected=("customers", "quotes"), actor="test")

        targets = (
            (self.target, customer_package, "customer-only-backup.sqlite3"),
            (self.root / "combined-target.sqlite3", combined_package, "combined-backup.sqlite3"),
        )
        for database_path, package, backup_name in targets:
            with self.subTest(package=package.name):
                local_customer_id = seed_target(database_path)
                service = BusinessSyncService(BusinessSyncRepository(database_path))
                preview = service.preview(package)
                service.apply(
                    package,
                    backup_path=self.root / backup_name,
                    actor="test",
                    expected_token=cast(str, preview["token"]),
                )
                with connect(database_path) as connection:
                    customer = connection.execute(
                        "SELECT id, name FROM customers WHERE sync_id = 'renamed-customer-sync-id'"
                    ).fetchone()
                    quote = connection.execute(
                        """
                        SELECT customer_id, customer_name, version
                        FROM quote_records
                        WHERE sync_id = 'renamed-quote-sync-id'
                        """
                    ).fetchone()
                self.assertEqual(tuple(customer), (local_customer_id, "客户新名称"))
                self.assertEqual(tuple(quote), (local_customer_id, "客户新名称", 5))

    def test_selected_quote_conflicts_increment_local_version_for_older_and_equal_packages(self) -> None:
        with connect(self.source) as connection:
            source_customer_id = connection.execute(
                "INSERT INTO customers (name, sync_id) VALUES (?, ?)",
                ("版本客户", "version-source-customer-id"),
            ).lastrowid
            for sync_id, bld_no, version, tax_price in (
                ("quote-equal-version", "VERSION-EQUAL", 5, 25),
                ("quote-older-version", "VERSION-OLDER", 2, 22),
            ):
                connection.execute(
                    """
                    INSERT INTO quote_records
                      (customer_id, customer_name, bld_no, product_model, tax_price, currency,
                       quote_date, remark, version, sync_id, quote_no, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_customer_id,
                        "版本客户",
                        bld_no,
                        bld_no,
                        tax_price,
                        "CNY",
                        "2026-07-19",
                        "incoming",
                        version,
                        sync_id,
                        f"Q-{bld_no}",
                        "2026-07-17 10:00:00",
                        "2026-07-19 10:00:00",
                    ),
                )
            connection.commit()

        with connect(self.target) as connection:
            local_customer_id = connection.execute(
                "INSERT INTO customers (name, sync_id) VALUES (?, ?)",
                ("版本客户", "version-target-customer-id"),
            ).lastrowid
            for sync_id, bld_no in (
                ("quote-equal-version", "VERSION-EQUAL"),
                ("quote-older-version", "VERSION-OLDER"),
            ):
                connection.execute(
                    """
                    INSERT INTO quote_records
                      (customer_id, customer_name, bld_no, product_model, tax_price, currency,
                       quote_date, remark, version, sync_id, quote_no, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        local_customer_id,
                        "版本客户",
                        bld_no,
                        bld_no,
                        10,
                        "CNY",
                        "2026-07-19",
                        "local",
                        5,
                        sync_id,
                        f"Q-{bld_no}",
                        "2026-07-17 10:00:00",
                        "2026-07-18 10:00:00",
                    ),
                )
            connection.commit()

        package = self.root / "quote-version-conflicts.tar.gz"
        BusinessSyncRepository(self.source).export(output_path=package, selected=("quotes",), actor="test")
        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(package)
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        self.assertEqual(cast(dict[str, int], summary["quotes"]["counts"])["conflict"], 2)

        result = service.apply(
            package,
            backup_path=self.root / "quote-version-backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
            selected_conflicts={"quotes": {"quote-equal-version", "quote-older-version"}},
        )
        self.assertEqual(result["quotes"]["updated"], 2)
        with connect(self.target) as connection:
            rows = connection.execute(
                """
                SELECT sync_id, version, tax_price, remark, customer_id, customer_name
                FROM quote_records
                ORDER BY sync_id
                """
            ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                ("quote-equal-version", 6, 25.0, "incoming", local_customer_id, "版本客户"),
                ("quote-older-version", 6, 22.0, "incoming", local_customer_id, "版本客户"),
            ],
        )

    def test_selected_quote_conflict_resolves_an_incoming_customer_change_by_name(self) -> None:
        with connect(self.source) as connection:
            source_customer_id = connection.execute(
                "INSERT INTO customers (name, sync_id) VALUES (?, ?)",
                ("客户 B", "source-customer-b"),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO quote_records
                  (customer_id, customer_name, bld_no, product_model, tax_price, currency,
                   quote_date, remark, version, sync_id, quote_no, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_customer_id,
                    "客户 B",
                    "MOVE-CUSTOMER",
                    "MOVE-CUSTOMER",
                    22,
                    "CNY",
                    "2026-07-19",
                    "incoming B",
                    2,
                    "quote-move-customer",
                    "Q-MOVE-CUSTOMER",
                    "2026-07-17 10:00:00",
                    "2026-07-19 10:00:00",
                ),
            )
            connection.commit()

        with connect(self.target) as connection:
            customer_a_id = connection.execute(
                "INSERT INTO customers (name, sync_id) VALUES (?, ?)",
                ("客户 A", "target-customer-a"),
            ).lastrowid
            customer_b_id = connection.execute(
                "INSERT INTO customers (name, sync_id) VALUES (?, ?)",
                ("客户 B", "target-customer-b"),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO quote_records
                  (customer_id, customer_name, bld_no, product_model, tax_price, currency,
                   quote_date, remark, version, sync_id, quote_no, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_a_id,
                    "客户 A",
                    "MOVE-CUSTOMER",
                    "MOVE-CUSTOMER",
                    10,
                    "CNY",
                    "2026-07-19",
                    "local A",
                    5,
                    "quote-move-customer",
                    "Q-MOVE-CUSTOMER",
                    "2026-07-17 10:00:00",
                    "2026-07-18 10:00:00",
                ),
            )
            connection.commit()

        package = self.root / "quote-customer-change.tar.gz"
        BusinessSyncRepository(self.source).export(output_path=package, selected=("quotes",), actor="test")
        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(package)
        result = service.apply(
            package,
            backup_path=self.root / "quote-customer-change-backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
            selected_conflicts={"quotes": {"quote-move-customer"}},
        )

        self.assertEqual(result["quotes"]["updated"], 1)
        with connect(self.target) as connection:
            row = connection.execute(
                """
                SELECT customer_id, customer_name, tax_price, remark, version
                FROM quote_records
                WHERE sync_id = 'quote-move-customer'
                """
            ).fetchone()
        self.assertEqual(tuple(row), (customer_b_id, "客户 B", 22.0, "incoming B", 6))

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

    def test_quote_package_with_unknown_customer_requires_mapping(self) -> None:
        package = self._package()
        service = BusinessSyncService(BusinessSyncRepository(self.target))

        preview = service.preview(package)
        self.assertEqual(preview["unresolved_customers"], ["同步客户"])
        self.assertEqual(preview["customer_options"], [])

        with self.assertRaisesRegex(ValueError, "新建或映射"):
            service.apply(
                package,
                backup_path=self.root / "backup.sqlite3",
                actor="test",
                expected_token=cast(str, preview["token"]),
            )

        result = service.apply(
            package,
            backup_path=self.root / "backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
            customer_mappings={"同步客户": None},
        )
        self.assertEqual(result["quotes"]["new"], 1)
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM customers WHERE name = '同步客户'").fetchone()[0], 1)

    def test_quote_package_unknown_customer_can_map_to_existing(self) -> None:
        package = self._package()
        with connect(self.target) as connection:
            connection.execute(
                "INSERT INTO customers (name, sync_id) VALUES (?, ?)",
                ("既有客户", "existing-customer-id"),
            )
            connection.commit()

        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(package)
        self.assertEqual(preview["unresolved_customers"], ["同步客户"])
        self.assertEqual(preview["customer_options"], ["既有客户"])

        result = service.apply(
            package,
            backup_path=self.root / "backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
            customer_mappings={"同步客户": "既有客户"},
        )
        self.assertEqual(result["quotes"]["new"], 1)
        with connect(self.target) as connection:
            row = connection.execute("SELECT customer_name FROM quote_records WHERE sync_id = 'quote-sync-id'").fetchone()
            self.assertEqual(row[0], "既有客户")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)

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

    def test_disabled_products_are_excluded_from_export_and_legacy_import(self) -> None:
        with connect(self.source) as connection:
            connection.execute(
                "INSERT INTO products (bld_no, active, created_at, updated_at) VALUES (?, 1, ?, ?)",
                ("ACTIVE-PRODUCT", "2026-08-09 10:00:00", "2026-08-09 10:00:00"),
            )
            connection.execute(
                "INSERT INTO products (bld_no, active, created_at, updated_at) VALUES (?, 0, ?, ?)",
                ("DISABLED-PRODUCT", "2026-08-09 10:00:00", "2026-08-09 10:00:00"),
            )
            disabled_row = dict(
                connection.execute("SELECT * FROM products WHERE bld_no = 'DISABLED-PRODUCT'").fetchone()
            )
            disabled_row.pop("id")
            connection.commit()

        exported = self.root / "active-products-only.tar.gz"
        repository = BusinessSyncRepository(self.source)
        repository.export(output_path=exported, selected=("products",), actor="test")
        _manifest, exported_payload = repository.read(exported)
        self.assertEqual(
            [row["bld_no"] for row in exported_payload["products"]],
            ["ACTIVE-PRODUCT"],
        )

        legacy = self._write_package({"products": [disabled_row]})
        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(legacy)
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        counts = cast(dict[str, int], summary["products"]["counts"])
        self.assertEqual(counts["ignored_inactive"], 1)
        self.assertEqual(counts["new"], 0)
        result = service.apply(
            legacy,
            backup_path=self.root / "disabled-product-backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
        )
        self.assertEqual(result["products"]["ignored_inactive"], 1)
        with connect(self.target) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM products WHERE bld_no = 'DISABLED-PRODUCT'"
                ).fetchone()[0],
                0,
            )

    def test_product_media_and_local_only_deactivation_use_business_sync(self) -> None:
        source_drawings = self.root / "source-drawings"
        source_images = self.root / "source-images"
        target_drawings = self.root / "target-drawings"
        target_images = self.root / "target-images"
        source_drawings.mkdir()
        source_images.mkdir()
        (source_drawings / "SYNC-PRODUCT.pdf").write_bytes(b"source-drawing")
        (source_images / "SYNC-PRODUCT.png").write_bytes(b"source-image")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()
        with connect(self.target) as connection:
            connection.execute(
                "INSERT INTO products (bld_no, active, created_at, updated_at) VALUES ('LOCAL-ONLY', 1, '2026-07-17 10:00:00', '2026-07-17 10:00:00')"
            )
            connection.execute(
                "INSERT INTO products (bld_no, active, created_at, updated_at) VALUES ('ALREADY-DISABLED', 0, '2026-07-17 10:00:00', '2026-07-17 10:00:00')"
            )
            connection.commit()
        package = self.root / "business-with-media.tar.gz"
        source_service = BusinessSyncService(
            BusinessSyncRepository(self.source, drawing_dir=source_drawings, image_dir=source_images)
        )
        source_service.export(
            output_path=package,
            selected=("products",),
            include_drawings=True,
            include_images=True,
            actor="test",
        )
        target_service = BusinessSyncService(
            BusinessSyncRepository(self.target, drawing_dir=target_drawings, image_dir=target_images)
        )
        preview = target_service.preview(package)
        self.assertEqual(
            preview["media"]["files"],
            {"drawings": 1, "product_images": 1, "material_drawings": 0},
        )
        summary = cast(dict[str, dict[str, object]], preview["summary"])
        self.assertEqual(cast(dict[str, int], summary["products"]["counts"])["local_only"], 1)
        original_tar_open = tarfile.open
        with patch.object(tarfile, "open", wraps=original_tar_open) as archive_open:
            result = target_service.apply(
                package,
                backup_path=self.root / "media-backup.sqlite3",
                actor="test",
                expected_token=cast(str, preview["token"]),
                include_drawings=True,
                include_images=True,
                deactivate_local_only=True,
            )
        read_modes = [call.args[1] for call in archive_open.call_args_list]
        self.assertEqual(read_modes.count("r:gz"), 2)
        self.assertEqual(result["products"]["deactivated"], 1)
        self.assertEqual((target_drawings / "SYNC-PRODUCT.pdf").read_bytes(), b"source-drawing")
        self.assertEqual((target_images / "SYNC-PRODUCT.png").read_bytes(), b"source-image")
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT active FROM products WHERE bld_no = 'LOCAL-ONLY'").fetchone()[0], 0)

    def test_material_drawings_follow_materials_export_preview_and_explicit_import(self) -> None:
        source_material_drawings = self.root / "source-material-drawings"
        target_material_drawings = self.root / "target-material-drawings"
        source_material_drawings.mkdir()
        target_material_drawings.mkdir()
        (source_material_drawings / "QD1000.pdf").write_bytes(b"source-material-drawing")
        local_extra = target_material_drawings / "LOCAL-EXTRA.pdf"
        local_extra.write_bytes(b"local-extra")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()

        package = self.root / "business-with-material-drawings.tar.gz"
        source_service = BusinessSyncService(
            BusinessSyncRepository(self.source, material_drawing_dir=source_material_drawings)
        )
        source_service.export(
            output_path=package,
            selected=("materials",),
            include_material_drawings=True,
            actor="test",
        )
        with tarfile.open(package, "r:gz") as archive:
            self.assertIn("data/material_drawings/QD1000.pdf", archive.getnames())

        target_service = BusinessSyncService(
            BusinessSyncRepository(self.target, material_drawing_dir=target_material_drawings)
        )
        preview = target_service.preview(package)
        self.assertTrue(preview["media"]["material_drawings"])
        self.assertEqual(
            preview["media"]["files"],
            {"drawings": 0, "product_images": 0, "material_drawings": 1},
        )
        result = target_service.apply(
            package,
            backup_path=self.root / "material-drawing-backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
            include_material_drawings=True,
        )

        self.assertEqual(result["materials"]["new"], 1)
        self.assertEqual(
            (target_material_drawings / "QD1000.pdf").read_bytes(),
            b"source-material-drawing",
        )
        self.assertEqual(local_extra.read_bytes(), b"local-extra")

    def test_material_drawings_are_not_exported_without_materials_dataset(self) -> None:
        source_material_drawings = self.root / "source-material-drawings-without-materials"
        source_material_drawings.mkdir()
        (source_material_drawings / "QD-NOT-EXPORTED.pdf").write_bytes(b"not-exported")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()

        package = self.root / "business-products-with-material-drawing-option.tar.gz"
        repository = BusinessSyncRepository(
            self.source,
            material_drawing_dir=source_material_drawings,
        )
        BusinessSyncService(repository).export(
            output_path=package,
            selected=("products",),
            include_material_drawings=True,
            actor="test",
        )

        manifest, _payload = repository.read(package)
        media = cast(dict[str, object], manifest["media"])
        files = cast(dict[str, int], media["files"])
        self.assertFalse(media["material_drawings"])
        self.assertEqual(files["material_drawings"], 0)
        with tarfile.open(package, "r:gz") as archive:
            self.assertFalse(
                any(name.startswith("data/material_drawings/") for name in archive.getnames())
            )

    def test_material_drawings_are_not_overwritten_when_import_is_not_selected(self) -> None:
        source_material_drawings = self.root / "source-material-drawings-not-selected"
        target_material_drawings = self.root / "target-material-drawings-not-selected"
        source_material_drawings.mkdir()
        target_material_drawings.mkdir()
        (source_material_drawings / "QD-KEEP-LOCAL.pdf").write_bytes(b"incoming")
        target = target_material_drawings / "QD-KEEP-LOCAL.pdf"
        extra_target = target_material_drawings / "LOCAL-EXTRA.pdf"
        target.write_bytes(b"local")
        extra_target.write_bytes(b"extra")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()

        package = self.root / "business-material-drawing-not-selected.tar.gz"
        BusinessSyncService(
            BusinessSyncRepository(self.source, material_drawing_dir=source_material_drawings)
        ).export(
            output_path=package,
            selected=("materials",),
            include_material_drawings=True,
            actor="test",
        )
        target_service = BusinessSyncService(
            BusinessSyncRepository(self.target, material_drawing_dir=target_material_drawings)
        )
        preview = target_service.preview(package)
        result = target_service.apply(
            package,
            backup_path=self.root / "material-drawing-not-selected-backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
            include_material_drawings=False,
        )

        self.assertEqual(result["materials"]["new"], 1)
        self.assertEqual(target.read_bytes(), b"local")
        self.assertEqual(extra_target.read_bytes(), b"extra")

    def test_legacy_v2_package_without_material_drawings_media_is_compatible(self) -> None:
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()
        generated = self.root / "generated-business-v2.tar.gz"
        BusinessSyncService(BusinessSyncRepository(self.source)).export(
            output_path=generated,
            selected=("materials",),
            actor="test",
        )
        with tarfile.open(generated, "r:gz") as archive:
            manifest_file = archive.extractfile("manifest.json")
            data_file = archive.extractfile("data.json")
            assert manifest_file is not None
            assert data_file is not None
            manifest = json.loads(manifest_file.read().decode("utf-8"))
            data_bytes = data_file.read()
        manifest["version"] = 2
        manifest["media"] = {
            "drawings": False,
            "product_images": False,
            "files": {"drawings": 0, "product_images": 0},
        }
        legacy = self.root / "legacy-business-v2.tar.gz"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "data.json").write_bytes(data_bytes)
            with tarfile.open(legacy, "w:gz") as archive:
                archive.add(root / "manifest.json", arcname="manifest.json")
                archive.add(root / "data.json", arcname="data.json")

        service = BusinessSyncService(BusinessSyncRepository(self.target))
        preview = service.preview(legacy)
        media = cast(dict[str, object], preview["media"])
        files = cast(dict[str, int], media["files"])
        self.assertFalse(media["material_drawings"])
        self.assertEqual(files.get("material_drawings", 0), 0)
        result = service.apply(
            legacy,
            backup_path=self.root / "legacy-v2-backup.sqlite3",
            actor="test",
            expected_token=cast(str, preview["token"]),
            include_material_drawings=True,
        )

        self.assertEqual(result["materials"]["new"], 1)

    def test_material_drawing_export_only_includes_root_lowercase_pdf_files(self) -> None:
        source_material_drawings = self.root / "source-material-drawing-filter"
        source_material_drawings.mkdir()
        (source_material_drawings / "ROOT.pdf").write_bytes(b"root")
        (source_material_drawings / "UPPER.PDF").write_bytes(b"upper")
        (source_material_drawings / "notes.txt").write_bytes(b"notes")
        hardlink_source = source_material_drawings / "HARDLINK-SOURCE.pdf"
        hardlink_source.write_bytes(b"hardlink")
        (source_material_drawings / "HARDLINK-ALIAS.pdf").hardlink_to(hardlink_source)
        nested = source_material_drawings / "nested"
        nested.mkdir()
        (nested / "NESTED.pdf").write_bytes(b"nested")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()

        package = self.root / "business-material-drawing-filter.tar.gz"
        repository = BusinessSyncRepository(
            self.source,
            material_drawing_dir=source_material_drawings,
        )
        repository.export(
            output_path=package,
            selected=("materials",),
            include_material_drawings=True,
            actor="test",
        )

        with tarfile.open(package, "r:gz") as archive:
            media_names = [
                name for name in archive.getnames() if name.startswith("data/material_drawings/")
            ]
        manifest, _payload = repository.read(package)
        media = cast(dict[str, object], manifest["media"])
        files = cast(dict[str, int], media["files"])
        self.assertEqual(media_names, ["data/material_drawings/ROOT.pdf"])
        self.assertEqual(files["material_drawings"], 1)

    def test_material_drawing_package_rejects_unsafe_path_and_count_mismatch(self) -> None:
        manifest: dict[str, object] = {
            "package_type": "bld_business_data",
            "version": 3,
            "datasets": ["materials"],
            "media": {
                "drawings": False,
                "product_images": False,
                "material_drawings": True,
                "files": {"drawings": 0, "product_images": 0, "material_drawings": 1},
            },
        }
        payload = {"materials": [{"sync_id": "unsafe-material"}]}
        unsafe = self._write_raw_package(
            name="unsafe-material-drawing.tar.gz",
            manifest=manifest,
            payload=payload,
            media={"data/material_drawings//tmp/escape.pdf": b"unsafe"},
        )
        with self.assertRaisesRegex(ValueError, "不安全的媒体路径"):
            BusinessSyncRepository.read(unsafe)

        media_manifest = cast(dict[str, object], manifest["media"])
        files = cast(dict[str, int], media_manifest["files"])
        files["material_drawings"] = 0
        mismatched = self._write_raw_package(
            name="mismatched-material-drawing.tar.gz",
            manifest=manifest,
            payload=payload,
            media={"data/material_drawings/SAFE.pdf": b"safe"},
        )
        with self.assertRaisesRegex(ValueError, "计数与实际内容不一致"):
            BusinessSyncRepository.read(mismatched)

    def test_business_sync_rejects_casefold_equivalent_media_targets(self) -> None:
        package = self._write_raw_package(
            name="casefold-collision-business-package.tar.gz",
            manifest={
                "package_type": "bld_business_data",
                "version": 3,
                "datasets": ["materials"],
                "media": {
                    "drawings": False,
                    "product_images": False,
                    "material_drawings": True,
                    "files": {"drawings": 0, "product_images": 0, "material_drawings": 2},
                },
            },
            payload={"materials": [{"sync_id": "casefold-material"}]},
            media={
                "data/material_drawings/A.pdf": b"first",
                "data/material_drawings/a.pdf": b"second",
            },
        )
        with self.assertRaisesRegex(ValueError, "指向同一目标"):
            BusinessSyncRepository.read(package)

    def test_business_sync_export_rejects_portable_media_name_collisions(self) -> None:
        source_material_drawings = self.root / "source-export-name-collision"
        source_material_drawings.mkdir()
        (source_material_drawings / "FIRST.pdf").write_bytes(b"first")
        (source_material_drawings / "SECOND.pdf").write_bytes(b"second")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()
        package = self.root / "export-name-collision.tar.gz"
        repository = BusinessSyncRepository(
            self.source,
            material_drawing_dir=source_material_drawings,
        )

        audit = Mock()
        with patch.dict(
            package_archive.export_package.__globals__,
            {"normalized_media_target": lambda _relative: "same", "log_event": audit},
        ):
            with self.assertRaisesRegex(ValueError, "指向同一目标"):
                repository.export(
                    output_path=package,
                    selected=("materials",),
                    include_material_drawings=True,
                    actor="test",
                )

        self.assertFalse(package.exists())
        audit.assert_not_called()

    def test_business_sync_rejects_package_version_newer_than_supported(self) -> None:
        package = self._write_raw_package(
            name="future-business-package.tar.gz",
            manifest={
                "package_type": "bld_business_data",
                "version": 4,
                "datasets": ["materials"],
            },
            payload={"materials": [{"sync_id": "future-material"}]},
        )
        with self.assertRaisesRegex(ValueError, "请先升级系统"):
            BusinessSyncRepository.read(package)

    def test_business_sync_rejects_package_resource_limit_overruns(self) -> None:
        package = self._write_package({"materials": [{"sync_id": "resource-limit-material"}]})
        limits = (
            "MAX_PACKAGE_MEMBER_COUNT",
            "MAX_PACKAGE_TOTAL_SIZE",
            "MAX_PACKAGE_METADATA_SIZE",
        )
        for limit in limits:
            with self.subTest(limit=limit):
                with patch.dict(package_archive.read_package.__globals__, {limit: 1}):
                    with self.assertRaisesRegex(ValueError, "格式或文件大小无效"):
                        BusinessSyncRepository.read(package)

    def test_business_sync_rejects_unsafe_tar_member_types_and_paths(self) -> None:
        manifest: dict[str, object] = {
            "package_type": "bld_business_data",
            "version": 3,
            "datasets": ["materials"],
            "media": {
                "drawings": False,
                "product_images": False,
                "material_drawings": True,
                "files": {"drawings": 0, "product_images": 0, "material_drawings": 1},
            },
        }
        payload = {"materials": [{"sync_id": "unsafe-member-material"}]}

        def write_case(name: str, members: list[tarfile.TarInfo]) -> Path:
            package = self.root / f"unsafe-member-{name}.tar.gz"
            manifest_bytes = json.dumps(manifest).encode()
            payload_bytes = json.dumps(payload).encode()
            with tarfile.open(package, "w:gz") as archive:
                manifest_member = tarfile.TarInfo("manifest.json")
                manifest_member.size = len(manifest_bytes)
                archive.addfile(manifest_member, io.BytesIO(manifest_bytes))
                payload_member = tarfile.TarInfo("data.json")
                payload_member.size = len(payload_bytes)
                archive.addfile(payload_member, io.BytesIO(payload_bytes))
                for member in members:
                    content = b"unsafe" if member.isfile() else None
                    member.size = len(content) if content is not None else 0
                    archive.addfile(member, io.BytesIO(content) if content is not None else None)
            return package

        regular_cases = {
            "non-pdf": "data/material_drawings/NOT-PDF.txt",
            "traversal": "data/material_drawings/../ESCAPE.pdf",
        }
        for name, member_name in regular_cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    BusinessSyncRepository.read(write_case(name, [tarfile.TarInfo(member_name)]))

        for name, member_type in (("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE)):
            with self.subTest(case=name):
                member = tarfile.TarInfo(f"data/material_drawings/{name}.pdf")
                member.type = member_type
                member.linkname = "data/material_drawings/target.pdf"
                with self.assertRaisesRegex(ValueError, "格式或文件大小无效"):
                    BusinessSyncRepository.read(write_case(name, [member]))

        duplicate_name = "data/material_drawings/DUPLICATE.pdf"
        with self.assertRaisesRegex(ValueError, "格式或文件大小无效"):
            BusinessSyncRepository.read(
                write_case(
                    "duplicate",
                    [tarfile.TarInfo(duplicate_name), tarfile.TarInfo(duplicate_name)],
                )
            )

    def test_material_drawing_import_overwrites_unique_portable_equivalent_target(self) -> None:
        source_material_drawings = self.root / "source-portable-target"
        target_material_drawings = self.root / "target-portable-target"
        source_material_drawings.mkdir()
        target_material_drawings.mkdir()
        (source_material_drawings / "INCOMING.pdf").write_bytes(b"incoming")
        existing = target_material_drawings / "LOCAL.pdf"
        existing.write_bytes(b"local")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()
        package = self.root / "portable-target.tar.gz"
        BusinessSyncRepository(
            self.source,
            material_drawing_dir=source_material_drawings,
        ).export(
            output_path=package,
            selected=("materials",),
            include_material_drawings=True,
            actor="test",
        )
        repository = BusinessSyncRepository(
            self.target,
            material_drawing_dir=target_material_drawings,
        )
        preview = repository.preview(package)

        with patch.dict(
            media_transaction.copy_requested_media.__globals__,
            {"normalized_media_target": lambda _relative: "same"},
        ):
            repository.apply(
                package,
                backup_path=self.root / "portable-target-backup.sqlite3",
                actor="test",
                expected_token=cast(str, preview["token"]),
                selected_conflicts={},
                include_material_drawings=True,
            )

        self.assertEqual(existing.read_bytes(), b"incoming")
        self.assertFalse((target_material_drawings / "INCOMING.pdf").exists())

    def test_material_drawing_import_rejects_ambiguous_portable_local_targets(self) -> None:
        source_material_drawings = self.root / "source-ambiguous-target"
        target_material_drawings = self.root / "target-ambiguous-target"
        source_material_drawings.mkdir()
        target_material_drawings.mkdir()
        (source_material_drawings / "INCOMING.pdf").write_bytes(b"incoming")
        (target_material_drawings / "FIRST.pdf").write_bytes(b"first")
        (target_material_drawings / "SECOND.pdf").write_bytes(b"second")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()
        package = self.root / "ambiguous-target.tar.gz"
        BusinessSyncRepository(
            self.source,
            material_drawing_dir=source_material_drawings,
        ).export(
            output_path=package,
            selected=("materials",),
            include_material_drawings=True,
            actor="test",
        )
        repository = BusinessSyncRepository(
            self.target,
            material_drawing_dir=target_material_drawings,
        )
        preview = repository.preview(package)

        with patch.dict(
            media_transaction.copy_requested_media.__globals__,
            {"normalized_media_target": lambda _relative: "same"},
        ):
            with self.assertRaisesRegex(ValueError, "重复文件"):
                repository.apply(
                    package,
                    backup_path=self.root / "ambiguous-target-backup.sqlite3",
                    actor="test",
                    expected_token=cast(str, preview["token"]),
                    selected_conflicts={},
                    include_material_drawings=True,
                )

    def test_export_audit_is_only_written_after_package_success(self) -> None:
        source_material_drawings = self.root / "source-export-audit"
        source_material_drawings.mkdir()
        (source_material_drawings / "AUDIT.pdf").write_bytes(b"audit")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()
        repository = BusinessSyncRepository(
            self.source,
            material_drawing_dir=source_material_drawings,
        )
        package = self.root / "audited-export.tar.gz"
        audit_details: list[str] = []

        def record_audit(_connection, _action, _entity_type, _entity_id, detail, *, actor):
            self.assertTrue(package.is_file())
            self.assertEqual(actor, "test")
            audit_details.append(detail)

        with patch.dict(package_archive.export_package.__globals__, {"log_event": record_audit}):
            repository.export(
                output_path=package,
                selected=("materials",),
                include_material_drawings=True,
                actor="test",
            )
        self.assertEqual(len(audit_details), 1)
        self.assertIn("物料图纸 1 个", audit_details[0])

        failed_package = self.root / "failed-export.tar.gz"
        audit = Mock()
        with (
            patch.object(repository, "_add_media_directory", side_effect=ValueError("collision")),
            patch.dict(package_archive.export_package.__globals__, {"log_event": audit}),
        ):
            with self.assertRaisesRegex(ValueError, "collision"):
                repository.export(
                    output_path=failed_package,
                    selected=("materials",),
                    include_material_drawings=True,
                    actor="test",
                )
        self.assertFalse(failed_package.exists())
        audit.assert_not_called()

        limited_package = self.root / "resource-limited-export.tar.gz"
        audit = Mock()
        with patch.dict(
            package_archive.export_package.__globals__,
            {"MAX_PACKAGE_TOTAL_SIZE": 1, "log_event": audit},
        ):
            with self.assertRaisesRegex(ValueError, "格式或文件大小无效"):
                repository.export(
                    output_path=limited_package,
                    selected=("materials",),
                    actor="test",
                )
        self.assertFalse(limited_package.exists())
        audit.assert_not_called()

        audit_failed_package = self.root / "audit-failed-export.tar.gz"

        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        with patch.dict(package_archive.export_package.__globals__, {"log_event": fail_audit}):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                repository.export(
                    output_path=audit_failed_package,
                    selected=("materials",),
                    actor="test",
                )
        self.assertFalse(audit_failed_package.exists())

        concurrently_replaced_package = self.root / "concurrently-replaced-export.tar.gz"

        def replace_then_fail(*_args, **_kwargs):
            replacement = self.root / "other-request-export.tmp"
            replacement.write_bytes(b"other request package")
            os.replace(replacement, concurrently_replaced_package)
            raise RuntimeError("audit failed after concurrent replacement")

        with patch.dict(package_archive.export_package.__globals__, {"log_event": replace_then_fail}):
            with self.assertRaisesRegex(RuntimeError, "concurrent replacement"):
                repository.export(
                    output_path=concurrently_replaced_package,
                    selected=("materials",),
                    actor="test",
                )
        self.assertEqual(concurrently_replaced_package.read_bytes(), b"other request package")

    def test_material_drawing_media_is_restored_when_database_apply_fails(self) -> None:
        source_material_drawings = self.root / "source-material-drawing-rollback"
        target_material_drawings = self.root / "target-material-drawing-rollback"
        source_material_drawings.mkdir()
        target_material_drawings.mkdir()
        (source_material_drawings / "EXISTING.pdf").write_bytes(b"incoming-existing")
        (source_material_drawings / "NEW.pdf").write_bytes(b"incoming-new")
        existing_target = target_material_drawings / "EXISTING.pdf"
        new_target = target_material_drawings / "NEW.pdf"
        existing_target.write_bytes(b"local-existing")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()

        package = self.root / "business-material-drawing-rollback.tar.gz"
        BusinessSyncRepository(
            self.source,
            material_drawing_dir=source_material_drawings,
        ).export(
            output_path=package,
            selected=("materials",),
            include_material_drawings=True,
            actor="test",
        )
        target_repository = BusinessSyncRepository(
            self.target,
            material_drawing_dir=target_material_drawings,
        )
        preview = target_repository.preview(package)

        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("forced audit failure")

        with patch.dict(database_apply.apply_package.__globals__, {"log_event": fail_audit}):
            with self.assertRaisesRegex(RuntimeError, "forced audit failure"):
                target_repository.apply(
                    package,
                    backup_path=self.root / "rollback-backup.sqlite3",
                    actor="test",
                    expected_token=cast(str, preview["token"]),
                    selected_conflicts={},
                    include_material_drawings=True,
                )

        self.assertEqual(existing_target.read_bytes(), b"local-existing")
        self.assertFalse(new_target.exists())
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM material_items").fetchone()[0], 0)

    def test_material_drawing_media_is_restored_when_later_media_write_fails(self) -> None:
        source_material_drawings = self.root / "source-material-drawing-write-failure"
        target_material_drawings = self.root / "target-material-drawing-write-failure"
        source_material_drawings.mkdir()
        target_material_drawings.mkdir()
        (source_material_drawings / "EXISTING.pdf").write_bytes(b"incoming-existing")
        (source_material_drawings / "NEW.pdf").write_bytes(b"incoming-new")
        existing_target = target_material_drawings / "EXISTING.pdf"
        new_target = target_material_drawings / "NEW.pdf"
        existing_target.write_bytes(b"local-existing")
        with connect(self.source) as connection:
            self._seed(connection)
            connection.commit()

        package = self.root / "business-material-drawing-write-failure.tar.gz"
        BusinessSyncRepository(
            self.source,
            material_drawing_dir=source_material_drawings,
        ).export(
            output_path=package,
            selected=("materials",),
            include_material_drawings=True,
            actor="test",
        )
        target_repository = BusinessSyncRepository(
            self.target,
            material_drawing_dir=target_material_drawings,
        )
        preview = target_repository.preview(package)
        original_copy_stream = target_repository._atomic_copy_stream
        copy_count = 0
        failed_sources = []

        def fail_second_copy(source, target):
            nonlocal copy_count
            copy_count += 1
            if copy_count == 2:
                failed_sources.append(source)
                raise OSError("forced second media write failure")
            original_copy_stream(source, target)

        with patch.object(target_repository, "_atomic_copy_stream", side_effect=fail_second_copy):
            with self.assertRaisesRegex(OSError, "forced second media write failure"):
                target_repository.apply(
                    package,
                    backup_path=self.root / "write-failure-backup.sqlite3",
                    actor="test",
                    expected_token=cast(str, preview["token"]),
                    selected_conflicts={},
                    include_material_drawings=True,
                )

        self.assertEqual(existing_target.read_bytes(), b"local-existing")
        self.assertFalse(new_target.exists())
        self.assertEqual(len(failed_sources), 1)
        self.assertTrue(failed_sources[0].closed)
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM material_items").fetchone()[0], 0)
