from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from app.database import connect
from app.modules.quotes.domain import QuoteValidationError, build_quote_draft
from app.modules.quotes.repository import SQLiteQuoteUnitOfWork
from app.modules.quotes.service import QuoteService, QuoteVersionConflictError
from app.migrations import run_migrations


class FakeQuoteImportPort:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def parse(self, path: Path, *, customer_name: str, currency: str) -> dict:
        return {"rows": self.rows, "customer_name": customer_name, "currency": currency, "path": path.name}

    def encode(self, rows: list[dict]) -> str:
        return json.dumps(rows)

    def decode(self, payload: str) -> list[dict]:
        return json.loads(payload)


class NoopImportLock:
    @contextmanager
    def __call__(self, _owner: str, _purpose: str):
        yield


class QuoteModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "quotes.sqlite3"
        with connect(self.db_path):
            pass
        self.service = QuoteService(
            lambda: SQLiteQuoteUnitOfWork(self.db_path),
            FakeQuoteImportPort(),
            NoopImportLock(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def quote_data(**overrides) -> dict:
        return {
            "customer_name": "Module Customer",
            "bld_no": "MODULE-001",
            "tax_price": "10.25",
            "net_price": "9.32",
            "currency": "USD",
            "quote_date": "2026-07-11",
            "source_type": "manual",
            **overrides,
        }

    def test_domain_validation_is_pure_and_preserves_partial_update_semantics(self):
        with self.assertRaisesRegex(QuoteValidationError, "customer_name"):
            build_quote_draft(self.quote_data(customer_name=""))
        with self.assertRaisesRegex(QuoteValidationError, "tax_price"):
            build_quote_draft(self.quote_data(tax_price="bad"))

        created = self.service.create(self.quote_data(remark="initial"), actor="module-test")
        updated = self.service.update(
            created.id,
            {"net_price": "", "remark": ""},
            actor="module-test",
            expected_version=1,
        )
        self.assertEqual(updated.tax_price, 10.25)
        self.assertIsNone(updated.net_price)
        self.assertEqual(updated.remark, "")
        self.assertEqual(updated.version, 2)

    def test_service_owns_transaction_revision_and_audit(self):
        created = self.service.create(self.quote_data(), actor="server-principal")
        self.assertEqual(created.version, 1)
        self.assertEqual(self.service.list_records({"customer_name": "Module"}).total, 1)
        self.assertEqual(self.service.latest(customer_name="Module Customer", bld_no="MODULE-001"), created)

        updated = self.service.update(
            created.id,
            {"tax_price": "10.75"},
            actor="server-principal",
            expected_version=created.version,
        )
        self.assertEqual(updated.version, 2)
        with self.assertRaises(QuoteVersionConflictError) as conflict:
            self.service.update(
                created.id,
                {"tax_price": "11.00"},
                actor="stale-client",
                expected_version=created.version,
            )
        self.assertEqual(conflict.exception.current_version, 2)

        with connect(self.db_path) as conn:
            revisions = conn.execute(
                "SELECT changed_by, before_json, after_json FROM quote_record_revisions WHERE quote_id = ?",
                (created.id,),
            ).fetchall()
            audit = conn.execute(
                "SELECT action, actor FROM audit_logs WHERE target_type = 'quote_record' ORDER BY id"
            ).fetchall()
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["changed_by"], "server-principal")
        self.assertIn('"version": 1', revisions[0]["before_json"])
        self.assertIn('"version": 2', revisions[0]["after_json"])
        self.assertEqual(
            [(row["action"], row["actor"]) for row in audit],
            [("新增报价记录", "server-principal"), ("修正报价记录", "server-principal")],
        )

    def test_concurrent_updates_cannot_silently_overwrite(self):
        created = self.service.create(self.quote_data(), actor="creator")

        def update(price: str):
            try:
                return self.service.update(
                    created.id,
                    {"tax_price": price},
                    actor=f"writer-{price}",
                    expected_version=1,
                )
            except QuoteVersionConflictError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, ["11.00", "12.00"]))
        records = [result for result in results if not isinstance(result, QuoteVersionConflictError)]
        conflicts = [result for result in results if isinstance(result, QuoteVersionConflictError)]
        self.assertEqual(len(records), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(records[0].version, 2)
        self.assertEqual(conflicts[0].current_version, 2)

    def test_system_attribution_cannot_be_modified(self):
        created = self.service.create(self.quote_data(), actor="creator")

        with self.assertRaisesRegex(QuoteValidationError, "由系统维护"):
            self.service.update(
                created.id,
                {"quoted_by": "spoofed", "source_type": "manual"},
                actor="api-principal",
                expected_version=created.version,
            )

        unchanged = self.service.get_record(created.id)
        self.assertEqual(unchanged.version, 1)
        self.assertEqual(unchanged.quoted_by, "creator")
        self.assertEqual(unchanged.source_type, "manual")

    def test_import_runs_through_same_domain_and_transaction(self):
        rows = [
            {**self.quote_data(bld_no="IMPORT-001"), "status": "valid"},
            {**self.quote_data(bld_no="IMPORT-SKIP"), "status": "invalid"},
        ]
        payload = json.dumps(rows)
        imported, skipped = self.service.apply_import_payload(payload, actor="importer")
        self.assertEqual((imported, skipped), (1, 1))
        page = self.service.list_records({"bld_no": "IMPORT-"})
        self.assertEqual([record.bld_no for record in page.records], ["IMPORT-001"])
        self.assertEqual(page.records[0].quoted_by, "importer")
        self.assertEqual(page.records[0].source_type, "excel")

    def test_historical_quote_table_gains_version_without_losing_rows(self):
        historical_path = Path(self.tmp.name) / "historical-quotes.sqlite3"
        connection = sqlite3.connect(historical_path)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
              id TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE quote_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_name TEXT NOT NULL,
              product_model TEXT NOT NULL,
              price REAL NOT NULL,
              currency TEXT NOT NULL,
              quote_date TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE api_idempotency_keys (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              principal_id TEXT NOT NULL,
              method TEXT NOT NULL,
              endpoint TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              request_hash TEXT NOT NULL,
              state TEXT NOT NULL,
              response_status INTEGER,
              response_body TEXT DEFAULT '',
              response_content_type TEXT DEFAULT 'application/json',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              UNIQUE(principal_id, method, endpoint, idempotency_key)
            );
            INSERT INTO quote_records
              (customer_name, product_model, price, currency, quote_date, created_at, updated_at)
            VALUES
              ('Historical Customer', 'HIST-001', 10.0, 'USD', '2026-01-01',
               '2026-01-01 00:00:00', '2026-01-01 00:00:00');
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations (id) VALUES (?)",
            [
                (migration_id,)
                for migration_id in (
                    "001_audit_log_actor",
                    "002_product_price_and_image",
                    "003_product_drawings",
                    "004_product_image_slots",
                    "005_internal_api_keys",
                    "006_shipment_recognition_jobs",
                    "007_product_status",
                    "008_internal_api_key_plaintext",
                    "009_quote_records",
                    "010_quote_record_bld_prices",
                    "011_customer_price_bld_index",
                    "012_scrub_internal_api_key_plaintext",
                    "013_api_principal_scopes_and_idempotency",
                )
            ],
        )
        connection.commit()
        run_migrations(connection)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(quote_records)")}
        idempotency_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(api_idempotency_keys)")
        }
        row = connection.execute("SELECT customer_name, version FROM quote_records").fetchone()
        connection.close()
        self.assertIn("version", columns)
        self.assertIn("response_headers", idempotency_columns)
        self.assertEqual(dict(row), {"customer_name": "Historical Customer", "version": 1})


if __name__ == "__main__":
    unittest.main()
