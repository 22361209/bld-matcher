from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.migrations import MIGRATIONS, run_migrations
from app.modules.costing.repository import initialize_costing_database
from app.modules.costing.schema import COSTING_TABLES, install_schema
from app.modules.costing.service import CostingService
from app.platform.permissions import LEGACY_ROLE_PERMISSIONS, effective_permissions, permission_groups
from app.security import landing_page_options, permission_landing_url


class CostingMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "historical-main.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def historical_main(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE products (id INTEGER PRIMARY KEY, code TEXT, price TEXT);
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, role TEXT);
            CREATE TABLE role_permissions (role_key TEXT, permission_key TEXT, PRIMARY KEY(role_key, permission_key));
            CREATE TABLE material_items (id INTEGER PRIMARY KEY, code TEXT);
            CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, action TEXT, actor TEXT);
            INSERT INTO products VALUES (42, 'KEEP-PRODUCT', '31.25');
            INSERT INTO users VALUES (7, 'keep-user', 'synthetic-hash', 'editor');
            INSERT INTO role_permissions VALUES ('editor', 'edit_products');
            INSERT INTO material_items VALUES (1, 'P-8057');
            INSERT INTO audit_logs VALUES (9, 'existing event', 'keep-user');
        """)
        connection.executemany("INSERT INTO schema_migrations(id) VALUES (?)", [
            (identifier,) for identifier, _migration in MIGRATIONS if identifier < "044_costing"
        ])
        connection.commit()
        return connection

    @staticmethod
    def prior_contents(connection):
        return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]
                for table in ("products", "users", "role_permissions", "audit_logs")}

    def test_main_upgrade_is_additive_idempotent_and_usable_by_cost_service(self):
        connection = self.historical_main()
        try:
            before = self.prior_contents(connection)
            run_migrations(connection)
            run_migrations(connection)
            self.assertEqual(self.prior_contents(connection), before)
            self.assertEqual(tuple(connection.execute("SELECT * FROM material_items").fetchone()), (1, "P-8057", None))
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(set(COSTING_TABLES).issubset(names))
            self.assertNotIn("costing_schema", names)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM schema_migrations WHERE id='044_costing'",
            ).fetchone()[0], 1)
            service = CostingService(self.path)
            record = service.create({"code": "KEEP-PRODUCT", "kind": "arm", "items": [
                {"name": "材料", "amount": "100"},
            ]}, actor="keep-user")
            self.assertEqual(service.get(record["id"])["minimum_tax_price"], "137.5")
            self.assertEqual(service.revisions(record["id"])[0]["actor"], "keep-user")
            self.assertEqual(self.prior_contents(connection), before)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()

    def test_schema_install_respects_callers_rollback(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            install_schema(connection)
            self.assertTrue(connection.in_transaction)
            connection.rollback()
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
        finally:
            connection.close()

    def test_trial_initializer_still_rejects_main_database_without_mutation(self):
        connection = self.historical_main()
        try:
            run_migrations(connection)
            before = list(connection.iterdump())
            with self.assertRaisesRegex(ValueError, "必须独立"):
                initialize_costing_database(self.path)
            self.assertEqual(list(connection.iterdump()), before)
        finally:
            connection.close()

    def test_trial_initializer_remains_idempotent_and_keeps_version_marker(self):
        initialize_costing_database(self.path)
        service = CostingService(self.path)
        record = service.create({"code": "TRIAL", "kind": "ball", "items": []}, actor="trial")
        initialize_costing_database(self.path)
        self.assertEqual(service.get(record["id"]), record)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT version FROM costing_schema").fetchall(), [(1,)])

    def test_cost_permissions_are_explicit_for_non_admin_and_support_cost_only_landing(self):
        levels = ("view_costs", "view_cost_list_full", "view_cost_details", "manage_costs")
        self.assertEqual(tuple(item.key for item in dict(permission_groups())["成本核算"]), levels)
        keys = set(levels)
        self.assertTrue(keys.issubset(effective_permissions("admin", set())))
        for role in ("editor", "user", "viewer"):
            self.assertFalse(keys & effective_permissions(role, LEGACY_ROLE_PERMISSIONS[role]))
        self.assertEqual(effective_permissions("cost-viewer", {"view_costs"}), frozenset({"view_costs"}))
        for index, permission in enumerate(levels):
            expected = set(levels[:index])
            with self.subTest(permission=permission):
                self.assertEqual(effective_permissions("custom", keys - {permission}), expected)
                self.assertEqual(effective_permissions("custom", keys, {permission: "deny"}), expected)
                self.assertEqual(effective_permissions("custom", keys - {permission}, {permission: "allow"}), keys)
        app = Flask(__name__)
        app.add_url_rule("/costs", endpoint="costing.index", view_func=lambda: "costs")
        with app.test_request_context("/"):
            self.assertEqual(landing_page_options({"view_costs"}), [
                {"key": "costs", "label": "成本核算", "endpoint": "costing.index"},
            ])
            self.assertEqual(permission_landing_url({"view_costs"}), "/costs")


if __name__ == "__main__":
    unittest.main()
