from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import connect
from app.modules.customers.domain import CustomerValidationError
from app.modules.customers.service import CustomerService


class CustomerModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "customers.sqlite3"
        with connect(self.db_path):
            pass
        self.service = CustomerService(lambda: connect(self.db_path))

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
        renamed = self.service.rename(customer.id, "宁波多迦", actor="tester")
        self.assertEqual(renamed.name, "宁波多迦")
        self.assertEqual(renamed.sync_id, customer.sync_id)
        with connect(self.db_path) as connection:
            name = connection.execute("SELECT customer_name FROM quote_records WHERE sync_id = 'q1'").fetchone()[0]
        self.assertEqual(name, "宁波多迦")
        self.service.create("博世", actor="tester")
        with self.assertRaisesRegex(CustomerValidationError, "已存在"):
            self.service.rename(customer.id, "博世", actor="tester")

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


if __name__ == "__main__":
    unittest.main()
