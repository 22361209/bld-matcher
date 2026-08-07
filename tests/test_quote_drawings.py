from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.database import connect
from app.modules.customer_products.repository import SQLiteCustomerProductUnitOfWork
from app.modules.customer_products.service import CustomerProductService
from app.modules.quotes.domain import QuoteValidationError
from app.modules.quotes.infrastructure import CustomerDrawingDirectoryAdapter
from app.modules.quotes.repository import SQLiteQuoteUnitOfWork
from app.modules.quotes.service import QuoteNotFoundError, QuoteService


class FakeQuoteImportPort:
    def parse(self, path: Path, *, customer_name: str, currency: str) -> dict:
        return {"rows": [], "counts": {"total": 0, "valid": 0, "invalid": 0}}

    def encode(self, rows: list[dict]) -> str:
        return json.dumps(rows)

    def decode(self, payload: str) -> list[dict]:
        return json.loads(payload)


class NoopImportLock:
    @contextmanager
    def __call__(self, _owner: str, _purpose: str):
        yield


class PermissiveCatalog:
    def exists(self, _value: str) -> bool:
        return True


class FakeCustomerDirectory:
    def __init__(self, ids: dict[str, int]) -> None:
        self.ids = {name.casefold(): customer_id for name, customer_id in ids.items()}

    def exists(self, customer_name: str) -> bool:
        return str(customer_name).casefold() in self.ids

    def find_id(self, customer_name: str) -> int | None:
        return self.ids.get(str(customer_name).casefold())


class QuoteDrawingLinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "quote-drawings.sqlite3"
        with connect(self.db_path) as connection:
            connection.executemany(
                "INSERT INTO customers (id, name, status, sync_id) VALUES (?, ?, 'active', ?)",
                [(1, "Module Customer", "cust-1"), (2, "Other Customer", "cust-2")],
            )
            connection.executemany(
                """
                INSERT INTO customer_products
                  (id, customer_id, sync_id, bld_no, customer_product_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, '2026-08-01 00:00:00', '2026-08-01 00:00:00')
                """,
                [
                    (1, 1, "product-1", "MODULE-001", "支架总成"),
                    (2, 2, "product-2", "OTHER-001", ""),
                ],
            )
            connection.executemany(
                """
                INSERT INTO customer_drawing_groups
                  (id, customer_product_id, customer_id, sync_id, kind, current_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, '2026-08-01 00:00:00', '2026-08-01 00:00:00')
                """,
                [
                    (1, 1, 1, "group-1", "customer", 2),
                    (2, 2, 2, "group-2", "bld", 1),
                    (3, 1, 1, "group-3", "bld", 1),
                ],
            )
            connection.executemany(
                """
                INSERT INTO customer_drawing_files
                  (id, group_id, sync_id, version_no, revision_label, original_name, storage_path,
                   content_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'application/pdf', '2026-08-01 00:00:00')
                """,
                [
                    (11, 1, "file-11", 1, "Rev A", "bracket-v1.pdf", "cust-1/drawings/group-1/v0001/a.pdf"),
                    (12, 1, "file-12", 2, "Rev B", "bracket-v2.pdf", "cust-1/drawings/group-1/v0002/b.pdf"),
                    (13, 2, "file-13", 1, "", "other-v1.pdf", "cust-2/drawings/group-2/v0001/c.pdf"),
                    (14, 3, "file-14", 1, "", "bld-v1.pdf", "cust-1/drawings/group-3/v0001/d.pdf"),
                ],
            )
            connection.commit()
        self.drawing_service = CustomerProductService(
            lambda: SQLiteCustomerProductUnitOfWork(self.db_path),
            storage=None,
        )
        factory_patch = patch(
            "app.modules.customer_products.factory.get_customer_product_service",
            return_value=self.drawing_service,
        )
        factory_patch.start()
        self.addCleanup(factory_patch.stop)
        self.service = QuoteService(
            lambda: SQLiteQuoteUnitOfWork(self.db_path),
            FakeQuoteImportPort(),
            NoopImportLock(),
            PermissiveCatalog(),
            FakeCustomerDirectory({"Module Customer": 1, "Other Customer": 2}),
            customer_drawing_directory=CustomerDrawingDirectoryAdapter(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def quote_data(**overrides) -> dict:
        return {
            "customer_name": "Module Customer",
            "bld_no": "MODULE-001",
            "tax_price": "10.25",
            "currency": "USD",
            "quote_date": "2026-08-01",
            "source_type": "manual",
            **overrides,
        }

    def _legacy_quote(self, customer_name: str = "module customer", quote_no: str = "") -> int:
        with connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO quote_records
                  (sync_id, customer_id, customer_name, product_model, currency, quote_date,
                   source_type, quote_no, version, created_at, updated_at)
                VALUES (?, NULL, ?, 'LEGACY-001', 'USD', '2026-07-01', 'manual', ?, 1,
                        '2026-07-01 00:00:00', '2026-07-01 00:00:00')
                """,
                (f"legacy-{customer_name}", customer_name, quote_no),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def _link_count(self, quote_id: int) -> int:
        with connect(self.db_path) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM quote_record_drawings WHERE quote_record_id = ?",
                    (quote_id,),
                ).fetchone()[0]
            )

    def test_link_drawing_persists_once_and_audits(self):
        record = self.service.create(self.quote_data(), actor="linker")
        self.assertEqual(record.customer_id, 1)

        linked = self.service.link_drawing(record.id, "11", actor="linker")
        self.assertEqual(linked.quote_no, record.quote_no)
        self.service.link_drawing(record.id, 11, actor="linker")

        self.assertEqual(self._link_count(record.id), 1)
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT drawing_file_id, created_by FROM quote_record_drawings WHERE quote_record_id = ?",
                (record.id,),
            ).fetchone()
            actions = connection.execute(
                "SELECT action FROM audit_logs WHERE target_type = 'quote_record' ORDER BY id"
            ).fetchall()
        self.assertEqual((row["drawing_file_id"], row["created_by"]), (11, "linker"))
        self.assertIn("关联报价图纸", [action["action"] for action in actions])

    def test_link_drawing_rejects_other_customers_file(self):
        record = self.service.create(self.quote_data(), actor="linker")
        with self.assertRaisesRegex(QuoteValidationError, "不属于该报价行的客户"):
            self.service.link_drawing(record.id, 13, actor="linker")
        self.assertEqual(self._link_count(record.id), 0)

    def test_link_drawing_accepts_both_drawing_kinds(self):
        record = self.service.create(self.quote_data(), actor="linker")
        self.service.link_drawing(record.id, 11, actor="linker")
        self.service.link_drawing(record.id, 14, actor="linker")
        self.assertEqual(self._link_count(record.id), 2)

    def test_link_drawing_rejects_unknown_file_or_quote(self):
        record = self.service.create(self.quote_data(), actor="linker")
        with self.assertRaisesRegex(QuoteValidationError, "图纸文件不存在"):
            self.service.link_drawing(record.id, 9999, actor="linker")
        with self.assertRaises(QuoteNotFoundError):
            self.service.link_drawing(9999, 11, actor="linker")

    def test_link_drawing_falls_back_to_customer_name_for_legacy_rows(self):
        legacy_id = self._legacy_quote()
        self.service.link_drawing(legacy_id, 12, actor="linker")
        self.assertEqual(self._link_count(legacy_id), 1)

        other_legacy_id = self._legacy_quote(customer_name="stranger")
        with self.assertRaisesRegex(QuoteValidationError, "不属于该报价行的客户"):
            self.service.link_drawing(other_legacy_id, 11, actor="linker")

    def test_unlink_drawing_removes_only_own_quote_link(self):
        first = self.service.create(self.quote_data(), actor="linker")
        second = self.service.create(self.quote_data(), actor="linker")
        self.service.link_drawing(first.id, 11, actor="linker")
        self.service.link_drawing(second.id, 12, actor="linker")
        link_id = next(iter(self.service.drawing_links_by_quote_no(first.quote_no)[first.id])).link.id

        self.service.unlink_drawing(first.id, link_id, actor="linker")
        self.assertEqual(self._link_count(first.id), 0)
        self.assertEqual(self._link_count(second.id), 1)

        with self.assertRaisesRegex(QuoteValidationError, "不存在或已解除"):
            self.service.unlink_drawing(first.id, link_id, actor="linker")
        second_link_id = next(iter(self.service.drawing_links_by_quote_no(second.quote_no)[second.id])).link.id
        with self.assertRaisesRegex(QuoteValidationError, "不存在或已解除"):
            self.service.unlink_drawing(first.id, second_link_id, actor="linker")
        with self.assertRaises(QuoteNotFoundError):
            self.service.unlink_drawing(9999, link_id, actor="linker")

    def test_delete_quote_cascades_drawing_links(self):
        record = self.service.create(self.quote_data(), actor="linker")
        self.service.link_drawing(record.id, 11, actor="linker")
        self.service.link_drawing(record.id, 12, actor="linker")
        self.assertEqual(self._link_count(record.id), 2)

        self.service.delete(record.id, actor="linker")
        self.assertEqual(self._link_count(record.id), 0)

    def test_drawing_links_by_quote_no_enriches_and_flags_newer_versions(self):
        record = self.service.create(self.quote_data(), actor="linker")
        self.service.link_drawing(record.id, 11, actor="linker")
        self.service.link_drawing(record.id, 12, actor="linker")

        views = self.service.drawing_links_by_quote_no(record.quote_no)[record.id]
        by_version = {view.file.version_no: view for view in views}
        self.assertEqual(set(by_version), {1, 2})
        older = by_version[1]
        self.assertEqual(older.file.direction_label, "客户图纸")
        self.assertEqual(older.file.title, "MODULE-001 支架总成")
        self.assertEqual(older.file.revision_label, "Rev A")
        self.assertEqual(older.file.original_name, "bracket-v1.pdf")
        self.assertEqual(older.file.customer_id, 1)
        self.assertTrue(older.file.has_newer_version)
        self.assertFalse(by_version[2].file.has_newer_version)

    def test_drawing_link_options_cover_all_product_slots_per_record(self):
        record = self.service.create(self.quote_data(), actor="linker")
        options = self.service.drawing_link_options_by_quote_no(record.quote_no)[record.id]
        self.assertEqual(len(options), 2)
        by_label = {option["direction_label"]: option for option in options}
        self.assertEqual(set(by_label), {"客户图纸", "BLD 图纸"})
        customer_slot = by_label["客户图纸"]
        self.assertEqual(customer_slot["title"], "MODULE-001 支架总成")
        self.assertEqual([version.version_no for version in customer_slot["versions"]], [2, 1])
        bld_slot = by_label["BLD 图纸"]
        self.assertEqual([version.version_no for version in bld_slot["versions"]], [1])

        stranger_id = self._legacy_quote(customer_name="stranger", quote_no="Q-STRANGER")
        self.assertEqual(
            self.service.drawing_link_options_by_quote_no("Q-STRANGER"),
            {stranger_id: []},
        )

    def test_customer_product_options_matches_by_customer_id_or_legacy_name(self):
        self.service.create(self.quote_data(customer_product_code="CUST-1"), actor="linker")
        self._legacy_quote()
        self.service.create(
            self.quote_data(customer_name="Other Customer", bld_no="OTHER-001"),
            actor="linker",
        )

        options = self.service.customer_product_options(1, "Module Customer")
        by_bld = {option["bld_no"]: option["customer_product_code"] for option in options}
        # customer_id 直配与 customer_name NOCASE 回退（legacy 行）双轨命中。
        self.assertEqual(by_bld, {"LEGACY-001": "", "MODULE-001": "CUST-1"})

        other_options = self.service.customer_product_options(2, "Other Customer")
        self.assertEqual([option["bld_no"] for option in other_options], ["OTHER-001"])


if __name__ == "__main__":
    unittest.main()
