from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import connect
from app.migrations import MIGRATIONS, run_migrations
from app.modules.customers.domain import CustomerValidationError
from app.modules.customers.infrastructure import QuoteCustomerReader
from app.modules.customers.service import CustomerService
from app.modules.quotes.repository import SQLiteQuoteUnitOfWork
from app.modules.quotes.service import QuoteService


class CustomerModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "customers.sqlite3"
        with connect(self.db_path):
            pass
        quote_service = QuoteService(
            lambda: SQLiteQuoteUnitOfWork(self.db_path),
            object(),
            object(),
            object(),
            object(),
            object(),
        )
        self.service = CustomerService(
            lambda: connect(self.db_path),
            QuoteCustomerReader(lambda: quote_service),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _add_quote(self, customer_name: str, sync_id: str = "q1") -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO quote_records (customer_name, product_model, currency, quote_date, sync_id, created_at, updated_at)
                VALUES (?, 'MODEL', 'CNY', '2026-07-28', ?, '2026-07-28', '2026-07-28')
                """,
                (customer_name, sync_id),
            )
            connection.commit()

    def test_create_normalizes_name_and_rejects_duplicates(self):
        customer = self.service.create("  宁波  多迦 ", actor="tester")
        self.assertEqual(customer.name, "宁波 多迦")
        self.assertTrue(customer.sync_id)
        with self.assertRaisesRegex(CustomerValidationError, "已存在"):
            self.service.create("宁波 多迦", actor="tester")
        with self.assertRaisesRegex(CustomerValidationError, "不能为空"):
            self.service.create("   ", actor="tester")

    def test_same_name_generates_same_sync_id(self):
        first = self.service.create("博世", actor="tester")
        other_path = Path(self.tmp.name) / "other.sqlite3"
        with connect(other_path):
            pass
        other = CustomerService(lambda: connect(other_path))
        second = other.create("博世", actor="tester")
        self.assertEqual(first.sync_id, second.sync_id)

    def test_rename_cascades_quote_records(self):
        customer = self.service.create("浙江多迦", actor="tester")
        self._add_quote("浙江多迦")
        renamed = self.service.rename(customer.id, "宁波多迦", reason="客户主体名称更新", actor="tester")
        self.assertEqual(renamed.name, "宁波多迦")
        self.assertEqual(renamed.sync_id, customer.sync_id)
        with connect(self.db_path) as connection:
            name = connection.execute("SELECT customer_name FROM quote_records WHERE sync_id = 'q1'").fetchone()[0]
        self.assertEqual(name, "宁波多迦")
        self.service.create("博世", actor="tester")
        with self.assertRaisesRegex(CustomerValidationError, "已存在"):
            self.service.rename(customer.id, "博世", reason="客户主体名称更新", actor="tester")

    def test_delete_blocked_when_quotes_reference_customer(self):
        customer = self.service.create("吉利", actor="tester")
        self._add_quote("吉利")
        with self.assertRaisesRegex(CustomerValidationError, "不能删除"):
            self.service.delete(customer.id, actor="tester")
        self.assertIsNotNone(self.service.find_by_name("吉利"))

    def test_lookup_matches_name_fragment_case_insensitive(self):
        self.service.create("宁波多迦", actor="tester")
        self.service.create("博世", actor="tester")
        self.assertEqual([c.name for c in self.service.lookup("多迦")], ["宁波多迦"])
        self.assertEqual(len(self.service.lookup("")), 2)
        self.assertEqual(self.service.lookup("不存在"), [])

    def test_profile_migration_backfills_historical_quote_customer_id(self):
        from app.migrations import _add_customer_profiles_documents_and_quote_contracts

        historical_path = Path(self.tmp.name) / "historical-customers.sqlite3"
        connection = sqlite3.connect(historical_path)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE customers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              sync_id TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE quote_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_name TEXT NOT NULL
            );
            INSERT INTO customers (name, sync_id, created_at, updated_at)
            VALUES ('宁波 多迦', 'customer-sync', '2026-07-28', '2026-07-28');
            INSERT INTO quote_records (customer_name) VALUES ('  宁波   多迦  ');
            """
        )

        _add_customer_profiles_documents_and_quote_contracts(connection)
        customer = connection.execute(
            "SELECT id, code, status, owner_username FROM customers"
        ).fetchone()
        quote = connection.execute("SELECT customer_id, customer_name FROM quote_records").fetchone()
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        connection.close()

        self.assertEqual(customer["code"], "")
        self.assertEqual(customer["status"], "active")
        self.assertEqual(customer["owner_username"], "")
        self.assertEqual(quote["customer_id"], customer["id"])
        self.assertEqual(quote["customer_name"], "宁波 多迦")
        self.assertTrue(
            {
                "customer_contacts",
                "customer_document_groups",
                "customer_document_files",
                "contract_documents",
            }.issubset(tables)
        )

    def test_connect_upgrades_database_at_migration_027_before_new_indexes(self):
        historical_path = Path(self.tmp.name) / "migration-027.sqlite3"
        connection = sqlite3.connect(historical_path)
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
              id TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE customers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              sync_id TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_customers_name ON customers(name COLLATE NOCASE);
            CREATE UNIQUE INDEX idx_customers_sync_id ON customers(sync_id);
            CREATE TABLE quote_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_name TEXT NOT NULL,
              bld_no TEXT DEFAULT '',
              customer_product_code TEXT DEFAULT '',
              product_model TEXT NOT NULL,
              tax_price REAL,
              net_price REAL,
              currency TEXT NOT NULL,
              moq INTEGER,
              quote_date TEXT NOT NULL,
              quoted_by TEXT DEFAULT '',
              source_type TEXT NOT NULL DEFAULT 'manual',
              source_text TEXT DEFAULT '',
              attachment_path TEXT DEFAULT '',
              remark TEXT DEFAULT '',
              sync_id TEXT NOT NULL,
              quote_no TEXT NOT NULL DEFAULT '',
              version INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            INSERT INTO customers (name, sync_id, created_at, updated_at)
            VALUES ('历史客户', 'historical-customer', '2026-07-28', '2026-07-28');
            INSERT INTO quote_records (
              customer_name, product_model, currency, quote_date, sync_id, created_at, updated_at
            ) VALUES (
              '历史客户', 'K100', 'CNY', '2026-07-28', 'historical-quote', '2026-07-28', '2026-07-28'
            );
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations (id) VALUES (?)",
            [
                (migration_id,)
                for migration_id, _migration in MIGRATIONS
                if int(migration_id.split("_", 1)[0]) < 28
            ],
        )
        connection.commit()
        connection.close()

        with connect(historical_path) as upgraded:
            customer_columns = {
                row["name"]
                for row in upgraded.execute("PRAGMA table_info(customers)").fetchall()
            }
            quote = upgraded.execute("SELECT customer_id FROM quote_records").fetchone()
            customer = upgraded.execute("SELECT id FROM customers").fetchone()
            migration = upgraded.execute(
                "SELECT 1 FROM schema_migrations WHERE id = '028_customer_profiles_documents_and_quote_contracts'"
            ).fetchone()

        self.assertTrue({"code", "status", "owner_username"}.issubset(customer_columns))
        self.assertEqual(quote["customer_id"], customer["id"])
        self.assertIsNotNone(migration)

    def test_integrity_migration_upgrades_an_already_applied_028_shape(self):
        historical_path = Path(self.tmp.name) / "migration-028.sqlite3"
        connection = sqlite3.connect(historical_path)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
              id TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE customers (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              code TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX idx_customers_code
              ON customers(code) WHERE code <> '';
            CREATE TABLE quote_records (
              id INTEGER PRIMARY KEY,
              customer_id INTEGER,
              customer_name TEXT NOT NULL
            );
            CREATE TABLE contract_documents (
              id INTEGER PRIMARY KEY,
              contract_no TEXT NOT NULL
            );
            CREATE TABLE customer_contacts (
              id INTEGER PRIMARY KEY,
              customer_id INTEGER NOT NULL,
              is_primary INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO customers (id, name, code) VALUES (1, 'ACME Corp', 'C-Case');
            INSERT INTO customers (id, name, code) VALUES (2, 'Second', 'c-case');
            INSERT INTO quote_records (id, customer_name) VALUES (1, '  ACME   Corp  ');
            INSERT INTO customer_contacts (id, customer_id, is_primary) VALUES (1, 1, 0);
            INSERT INTO customer_contacts (id, customer_id, is_primary) VALUES (2, 1, 0);
            INSERT INTO customer_contacts (id, customer_id, is_primary) VALUES (3, 2, 1);
            INSERT INTO customer_contacts (id, customer_id, is_primary) VALUES (4, 2, 1);
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations (id) VALUES (?)",
            [
                (migration_id,)
                for migration_id, _migration in MIGRATIONS
                if int(migration_id.split("_", 1)[0]) <= 28
            ],
        )
        connection.commit()

        run_migrations(connection)

        contract_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(contract_documents)").fetchall()
        }
        quote = connection.execute(
            "SELECT customer_id, customer_name FROM quote_records WHERE id = 1"
        ).fetchone()
        codes = {
            int(row["id"]): str(row["code"])
            for row in connection.execute("SELECT id, code FROM customers ORDER BY id")
        }
        primary_ids = {
            int(row["customer_id"]): int(row["id"])
            for row in connection.execute(
                "SELECT id, customer_id FROM customer_contacts WHERE is_primary = 1 ORDER BY id"
            )
        }
        self.assertTrue(
            {"source_snapshot_json", "source_snapshot_sha256"}.issubset(contract_columns)
        )
        self.assertEqual((quote["customer_id"], quote["customer_name"]), (1, "ACME Corp"))
        self.assertEqual(codes, {1: "C-Case", 2: ""})
        self.assertEqual(primary_ids, {1: 1, 2: 3})
        self.assertIsNotNone(
            connection.execute(
                "SELECT 1 FROM schema_migrations WHERE id = '029_customer_workspace_integrity'"
            ).fetchone()
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO customers (id, name, code) VALUES (3, 'Third', 'c-case')"
            )
        connection.close()

    def test_repair_migration_runs_after_an_already_recorded_029(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
              id TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE customers (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              code TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX idx_customers_code
              ON customers(code COLLATE NOCASE) WHERE code <> '';
            CREATE TABLE customer_contacts (
              id INTEGER PRIMARY KEY,
              customer_id INTEGER NOT NULL,
              is_primary INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE quote_records (
              id INTEGER PRIMARY KEY,
              customer_id INTEGER,
              customer_name TEXT NOT NULL
            );
            INSERT INTO customers (id, name, code) VALUES (1, 'Zero Primary', 'C-1');
            INSERT INTO customers (id, name, code) VALUES (2, 'Many Primary', 'C-2');
            INSERT INTO customer_contacts (id, customer_id, is_primary) VALUES (1, 1, 0);
            INSERT INTO customer_contacts (id, customer_id, is_primary) VALUES (2, 1, 0);
            INSERT INTO customer_contacts (id, customer_id, is_primary) VALUES (3, 2, 1);
            INSERT INTO customer_contacts (id, customer_id, is_primary) VALUES (4, 2, 1);
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations (id) VALUES (?)",
            [
                (migration_id,)
                for migration_id, _migration in MIGRATIONS
                if int(migration_id.split("_", 1)[0]) <= 29
            ],
        )
        connection.commit()

        run_migrations(connection)

        primary_ids = {
            int(row["customer_id"]): int(row["id"])
            for row in connection.execute(
                "SELECT id, customer_id FROM customer_contacts WHERE is_primary = 1 ORDER BY id"
            )
        }
        migration = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE id = '030_repair_customer_workspace_integrity'"
        ).fetchone()
        self.assertEqual(primary_ids, {1: 1, 2: 3})
        self.assertIsNotNone(migration)
        connection.close()


if __name__ == "__main__":
    unittest.main()
