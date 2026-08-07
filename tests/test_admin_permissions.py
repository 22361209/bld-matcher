from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import connect
from app.migrations import (
    MIGRATIONS,
    _add_editable_roles_and_user_permission_overrides,
    _grant_view_product_prices_to_existing_roles,
    _revoke_view_product_prices_permission,
    _split_granular_permissions,
)
from app.modules.admin.persistence import ensure_default_admin, get_user
from app.modules.admin.repository import SQLiteAdminUnitOfWork
from app.modules.admin.service import AdminService
from app.platform.permissions import (
    ADMIN_ROLE_KEY,
    ALL_PERMISSION_KEYS,
    LEGACY_ROLE_PERMISSIONS,
    effective_permissions,
    permission_groups,
)


class _UpdateReader:
    source_name = "test"

    def read(self) -> list[dict[str, object]]:
        return []


class AdminPermissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "data" / "permissions.sqlite3"
        with connect(self.database_path) as connection:
            ensure_default_admin(connection, username="root-admin", password="root-password")
            self.admin_id = int(
                connection.execute("SELECT id FROM users WHERE username = 'root-admin'").fetchone()[0]
            )
        self.service = AdminService(
            lambda: SQLiteAdminUnitOfWork(self.database_path),
            _UpdateReader(),
            lambda _stored, _password: False,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _save_user(
        self,
        username: str,
        role: str,
        *,
        overrides: dict[str, str] | None = None,
    ) -> int:
        return self.service.save_user(
            {
                "username": username,
                "display_name": username,
                "password": "user-password",
                "role": role,
                "active": "1",
                "permission_overrides": overrides or {},
            },
            actor="root-admin",
            actor_user_id=self.admin_id,
        )

    def _edit_user(
        self,
        user_id: int,
        username: str,
        role: str,
        *,
        active: str = "1",
        overrides: dict[str, str] | None = None,
        actor_user_id: int | None = None,
    ) -> int:
        return self.service.save_user(
            {
                "id": user_id,
                "username": username,
                "display_name": username,
                "password": "",
                "role": role,
                "active": active,
                "permission_overrides": overrides if overrides is not None else {},
            },
            actor="root-admin",
            actor_user_id=self.admin_id if actor_user_id is None else actor_user_id,
        )

    def test_migration_preserves_legacy_roles_and_permissions(self) -> None:
        for role_key in ("editor", "user", "viewer"):
            user_id = self._save_user(f"legacy-{role_key}", role_key)
            with connect(self.database_path) as connection:
                user = get_user(connection, user_id)
            assert user is not None
            self.assertEqual(set(user["permissions"]), LEGACY_ROLE_PERMISSIONS[role_key])
            self.assertEqual(user["permission_overrides"], {})

        with connect(self.database_path) as connection:
            admin = get_user(connection, self.admin_id)
            roles = {
                row["role_key"]: row
                for row in connection.execute("SELECT * FROM roles").fetchall()
            }
            migration = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE id = ?",
                ("031_editable_roles_and_user_permission_overrides",),
            ).fetchone()
        assert admin is not None
        self.assertEqual(set(admin["permissions"]), ALL_PERMISSION_KEYS)
        self.assertEqual(roles[ADMIN_ROLE_KEY]["is_system"], 1)
        self.assertEqual(roles["editor"]["is_system"], 0)
        self.assertIsNotNone(migration)
        self.assertEqual(MIGRATIONS[-1][0], "036_customer_products")

    def test_migration_seeds_unknown_historical_roles_idempotently(self) -> None:
        historical_path = Path(self.temporary.name) / "historical.sqlite3"
        connection = sqlite3.connect(historical_path)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE users (
              id INTEGER PRIMARY KEY,
              username TEXT NOT NULL,
              role TEXT NOT NULL,
              active INTEGER NOT NULL
            );
            INSERT INTO users VALUES (1, 'legacy', 'warehouse', 1);
            """
        )
        _add_editable_roles_and_user_permission_overrides(connection)
        _add_editable_roles_and_user_permission_overrides(connection)
        role = connection.execute(
            "SELECT name, is_system FROM roles WHERE role_key = 'warehouse'"
        ).fetchone()
        counts = connection.execute(
            "SELECT COUNT(*) FROM roles WHERE role_key = 'warehouse'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(role["name"], "warehouse")
        self.assertEqual(role["is_system"], 0)
        self.assertEqual(counts, 1)

    def test_migration_allows_partial_historical_database_without_users(self) -> None:
        historical_path = Path(self.temporary.name) / "partial.sqlite3"
        connection = sqlite3.connect(historical_path)
        connection.row_factory = sqlite3.Row

        _add_editable_roles_and_user_permission_overrides(connection)
        _add_editable_roles_and_user_permission_overrides(connection)

        admin = connection.execute(
            "SELECT is_system FROM roles WHERE role_key = ?",
            (ADMIN_ROLE_KEY,),
        ).fetchone()
        connection.close()
        self.assertIsNotNone(admin)
        self.assertEqual(admin["is_system"], 1)

    def test_migration_032_grants_view_product_prices_to_existing_roles(self) -> None:
        historical_path = Path(self.temporary.name) / "historical-032.sqlite3"
        connection = sqlite3.connect(historical_path)
        connection.row_factory = sqlite3.Row
        _add_editable_roles_and_user_permission_overrides(connection)
        connection.execute("DELETE FROM role_permissions WHERE permission = 'view_product_prices'")
        connection.execute(
            """
            INSERT INTO roles (role_key, name, description, is_system, created_at, updated_at)
            VALUES ('custom-role', '自建角色', '', 0, '2026-01-01', '2026-01-01')
            """
        )

        _grant_view_product_prices_to_existing_roles(connection)
        _grant_view_product_prices_to_existing_roles(connection)

        granted = {
            row["role_key"]
            for row in connection.execute(
                "SELECT role_key FROM role_permissions WHERE permission = 'view_product_prices'"
            ).fetchall()
        }
        counts = connection.execute(
            "SELECT COUNT(*) FROM role_permissions WHERE role_key = 'custom-role'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(granted, {"editor", "user", "viewer", "custom-role"})
        self.assertEqual(counts, 1)

    def test_migration_033_revokes_view_product_prices_everywhere(self) -> None:
        historical_path = Path(self.temporary.name) / "historical-033.sqlite3"
        connection = sqlite3.connect(historical_path)
        connection.row_factory = sqlite3.Row
        _add_editable_roles_and_user_permission_overrides(connection)
        _grant_view_product_prices_to_existing_roles(connection)
        connection.execute(
            """
            INSERT INTO user_permission_overrides (user_id, permission, effect, updated_at)
            VALUES (1, 'view_product_prices', 'allow', '2026-01-01')
            """
        )

        _revoke_view_product_prices_permission(connection)
        _revoke_view_product_prices_permission(connection)

        remaining = connection.execute(
            "SELECT COUNT(*) FROM role_permissions WHERE permission = 'view_product_prices'"
        ).fetchone()[0]
        remaining_overrides = connection.execute(
            "SELECT COUNT(*) FROM user_permission_overrides WHERE permission = 'view_product_prices'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(remaining, 0)
        self.assertEqual(remaining_overrides, 0)

    def test_migration_034_maps_and_retires_old_permissions(self) -> None:
        historical_path = Path(self.temporary.name) / "historical-034.sqlite3"
        connection = sqlite3.connect(historical_path)
        connection.row_factory = sqlite3.Row
        _add_editable_roles_and_user_permission_overrides(connection)
        timestamp = "2026-01-01"
        old_role_permissions = (
            "manage_customers",
            "manage_customer_prices",
            "view_customer_prices",
            "generate_purchase_contract",
            "manage_materials",
            "recognize_shipments",
        )
        connection.executemany(
            "INSERT OR IGNORE INTO role_permissions (role_key, permission, created_at) VALUES ('editor', ?, ?)",
            ((permission, timestamp) for permission in old_role_permissions),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO user_permission_overrides (user_id, permission, effect, updated_at) VALUES (1, ?, 'deny', ?)",
            (("manage_customers", timestamp), ("manage_customer_prices", timestamp)),
        )

        _split_granular_permissions(connection)
        _split_granular_permissions(connection)

        editor_permissions = {
            row["permission"]
            for row in connection.execute(
                "SELECT permission FROM role_permissions WHERE role_key = 'editor'"
            ).fetchall()
        }
        overrides = {
            row["permission"]: row["effect"]
            for row in connection.execute("SELECT * FROM user_permission_overrides").fetchall()
        }
        retired = (
            "manage_customers",
            "manage_customer_prices",
            "generate_purchase_contract",
            "manage_materials",
            "recognize_shipments",
            "generate_shipping_notice",
        )
        connection.close()

        expected_new = {
            "view_customers",
            "add_customers",
            "edit_customers",
            "delete_customers",
            "add_customer_prices",
            "edit_customer_prices",
            "delete_customer_prices",
            "view_price_history",
            "view_contracts",
            "generate_contract",
            "manage_material_items",
            "manage_tube_items",
        }
        self.assertTrue(expected_new.issubset(editor_permissions))
        self.assertFalse(set(retired) & editor_permissions)
        self.assertFalse(set(retired) & set(overrides))
        for key in ("view_customers", "add_customers", "edit_customers", "delete_customers"):
            self.assertEqual(overrides.get(key), "deny")
        for key in ("add_customer_prices", "edit_customer_prices", "delete_customer_prices"):
            self.assertEqual(overrides.get(key), "deny")

    def test_role_changes_and_user_overrides_recalculate_immediately(self) -> None:
        role_key = self.service.save_role(
            {"name": "询价专员", "description": "处理询价"},
            ["generate_match", "manage_aliases"],
            actor="root-admin",
        )
        user_id = self._save_user(
            "quote-specialist",
            role_key,
            overrides={"manage_aliases": "deny", "view_customer_prices": "allow"},
        )
        with connect(self.database_path) as connection:
            user = get_user(connection, user_id)
        assert user is not None
        self.assertEqual(
            set(user["permissions"]),
            {"generate_match", "view_customer_prices"},
        )

        self.service.save_role(
            {"role_key": role_key, "name": "询价专员", "description": "询价与合同"},
            ["manage_aliases", "generate_contract"],
            actor="root-admin",
        )
        with connect(self.database_path) as connection:
            user = get_user(connection, user_id)
        assert user is not None
        self.assertEqual(
            set(user["permissions"]),
            {"generate_contract", "view_customer_prices"},
        )
        with self.assertRaisesRegex(ValueError, "仍有账号"):
            self.service.delete_role(role_key, actor="root-admin")

        self._edit_user(
            user_id,
            "quote-specialist",
            "viewer",
            overrides={"manage_aliases": "deny", "view_customer_prices": "allow"},
        )
        with connect(self.database_path) as connection:
            user = get_user(connection, user_id)
        assert user is not None
        self.assertEqual(set(user["permissions"]), {"view_customer_prices"})
        self.assertEqual(
            user["permission_overrides"],
            {"manage_aliases": "deny", "view_customer_prices": "allow"},
        )
        self.service.delete_role(role_key, actor="root-admin")
        with connect(self.database_path) as connection:
            self.assertIsNone(
                connection.execute("SELECT 1 FROM roles WHERE role_key = ?", (role_key,)).fetchone()
            )

    def test_fixed_admin_and_last_admin_guards_cannot_be_bypassed(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能停用当前"):
            self._edit_user(
                self.admin_id,
                "root-admin",
                ADMIN_ROLE_KEY,
                active="0",
            )
        with self.assertRaisesRegex(ValueError, "不能移除当前"):
            self._edit_user(self.admin_id, "root-admin", "viewer")
        with self.assertRaisesRegex(ValueError, "至少需要保留"):
            self._edit_user(
                self.admin_id,
                "root-admin",
                "viewer",
                actor_user_id=-1,
            )
        with self.assertRaisesRegex(ValueError, "系统固定"):
            self.service.save_role(
                {"role_key": ADMIN_ROLE_KEY, "name": "新管理员", "description": ""},
                [],
                actor="root-admin",
            )
        with self.assertRaisesRegex(ValueError, "系统固定"):
            self.service.delete_role(ADMIN_ROLE_KEY, actor="root-admin")
        with self.assertRaisesRegex(ValueError, "只能由管理员"):
            self.service.save_role(
                {"name": "越权角色", "description": ""},
                ["manage_users"],
                actor="root-admin",
            )

        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO user_permission_overrides (user_id, permission, effect, updated_at)
                VALUES (?, 'edit_products', 'deny', '2026-07-30')
                """,
                (self.admin_id,),
            )
            connection.commit()
            admin = get_user(connection, self.admin_id)
        assert admin is not None
        self.assertEqual(set(admin["permissions"]), ALL_PERMISSION_KEYS)

    def test_role_and_account_permission_changes_are_audited_without_passwords(self) -> None:
        role_key = self.service.save_role(
            {"name": "审计角色", "description": "测试日志"},
            ["view_logs"],
            actor="root-admin",
        )
        self._save_user(
            "audit-user",
            role_key,
            overrides={"generate_match": "allow"},
        )
        with connect(self.database_path) as connection:
            logs = connection.execute(
                "SELECT action, actor, detail FROM audit_logs WHERE actor = 'root-admin' ORDER BY id"
            ).fetchall()
        actions = {row["action"] for row in logs}
        details = "\n".join(str(row["detail"]) for row in logs)
        self.assertIn("新增角色", actions)
        self.assertIn("新增账号", actions)
        self.assertIn("generate_match=allow", details)
        self.assertNotIn("user-password", details)

    def test_effective_permission_precedence_truth_table(self) -> None:
        role_permissions = {"generate_match", "manage_aliases"}
        self.assertEqual(
            effective_permissions("custom", role_permissions),
            frozenset(role_permissions),
        )
        self.assertEqual(
            effective_permissions(
                "custom",
                role_permissions,
                {"generate_match": "deny", "view_logs": "allow"},
            ),
            frozenset({"manage_aliases", "view_logs"}),
        )

    def test_new_account_never_defaults_to_admin_without_non_system_roles(self) -> None:
        for role_key in ("editor", "user", "viewer"):
            self.service.delete_role(role_key, actor="root-admin")

        page = self.service.access_page()

        self.assertFalse(page.can_create_user)
        self.assertEqual(page.default_role_key, "")
        self.assertEqual([role["role_key"] for role in page.roles], [ADMIN_ROLE_KEY])

        template = (
            Path(__file__).resolve().parents[1] / "templates" / "users.html"
        ).read_text()
        self.assertIn("{% if not is_editing_user and not can_create_user %}", template)
        self.assertIn('<option value="" selected disabled>请选择可用角色</option>', template)

    def test_access_page_follows_page_javascript_and_submit_wait_protocols(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "pages" / "users.js").read_text()
        template = (root / "templates" / "users.html").read_text()

        self.assertIn('body[data-page="admin.users"]', script)
        self.assertEqual(template.count("data-submit-wait data-submit-wait-text="), 4)
        self.assertEqual(template.count("data-submit-wait-message"), 4)

    def test_account_permission_matrix_has_stable_headers_groups_and_accessible_controls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "users.html").read_text()
        account_table = re.search(
            r'<table\b[^>]*aria-labelledby="account-permissions-title"[^>]*>(.*?)</table>',
            template,
            re.DOTALL,
        )

        self.assertIsNotNone(account_table)
        assert account_table is not None
        header = re.search(r"<thead\b[^>]*>(.*?)</thead>", account_table.group(1), re.DOTALL)
        self.assertIsNotNone(header)
        assert header is not None
        headings = (
            "功能权限",
            "角色基线",
            "跟随角色",
            "额外授权",
            "明确禁止",
            "最终结果",
        )
        self.assertEqual(header.group(1).count('scope="col"'), len(headings))
        positions = [header.group(1).index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))

        self.assertEqual(len(permission_groups()), 5)
        self.assertIn("{% set group_index = loop.index %}", account_table.group(1))
        self.assertIn('id="account-permission-group-{{ group_index }}"', template)
        self.assertIn('href="#account-permission-group-{{ loop.index }}"', template)
        group_loop = account_table.group(1).index(
            "{% for group_name, definitions in permission_groups %}"
        )
        group_body = account_table.group(1).index("<tbody", group_loop)
        permission_loop = account_table.group(1).index(
            "{% for permission in definitions %}", group_body
        )
        permission_loop_end = account_table.group(1).index("{% endfor %}", permission_loop)
        group_body_end = account_table.group(1).index("</tbody>", permission_loop_end)
        group_loop_end = account_table.group(1).index("{% endfor %}", group_body_end)
        self.assertLess(group_loop, group_body)
        self.assertLess(group_body, permission_loop)
        self.assertLess(permission_loop_end, group_body_end)
        self.assertLess(group_body_end, group_loop_end)
        for choice in ("跟随角色", "额外授权", "明确禁止"):
            self.assertIn(
                f'aria-label="{{{{ permission.label }}}}－{choice}"',
                account_table.group(1),
            )

    def test_role_permission_matrix_has_stable_groups_and_accessible_controls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "users.html").read_text()
        role_table = re.search(
            r'<table\b[^>]*aria-labelledby="role-permissions-title"[^>]*>(.*?)</table>',
            template,
            re.DOTALL,
        )

        self.assertIsNotNone(role_table)
        assert role_table is not None
        self.assertIn("功能权限", role_table.group(1))
        self.assertIn("授予角色", role_table.group(1))
        self.assertIn("{% set group_index = loop.index %}", role_table.group(1))
        self.assertIn('id="role-permission-group-{{ group_index }}"', template)
        self.assertIn('href="#role-permission-group-{{ loop.index }}"', template)
        group_loop = role_table.group(1).index(
            "{% for group_name, definitions in permission_groups %}"
        )
        group_body = role_table.group(1).index("<tbody", group_loop)
        permission_loop = role_table.group(1).index(
            "{% for permission in definitions %}", group_body
        )
        permission_loop_end = role_table.group(1).index("{% endfor %}", permission_loop)
        group_body_end = role_table.group(1).index("</tbody>", permission_loop_end)
        group_loop_end = role_table.group(1).index("{% endfor %}", group_body_end)
        self.assertLess(group_loop, group_body)
        self.assertLess(group_body, permission_loop)
        self.assertLess(permission_loop_end, group_body_end)
        self.assertLess(group_body_end, group_loop_end)
        self.assertIn(
            'aria-label="授予角色：{{ permission.label }}"',
            role_table.group(1),
        )

    def test_permission_matrix_preserves_page_assets_and_form_submission_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "users.html").read_text()
        script = (root / "static" / "pages" / "users.js").read_text()
        stylesheet = (root / "static" / "pages" / "users.css").read_text()

        self.assertIn("filename='pages/users.css'", template)
        self.assertIn("filename='pages/users.js'", template)
        self.assertIn('<script type="module"', template)
        self.assertEqual(
            template.count(
                '<input type="hidden" name="permission_selection_present" value="1">'
            ),
            2,
        )
        for value in ("inherit", "allow", "deny"):
            self.assertIn(
                f'name="permission_{{{{ permission.key }}}}" value="{value}"',
                template,
            )
        self.assertIn('name="permissions" value="{{ permission.key }}"', template)
        self.assertIn("[data-permission-row]", script)
        self.assertIn('input[type="radio"][value="inherit"]', script)
        mobile_styles = stylesheet.split("@media (max-width: 760px) {", 1)[1]
        self.assertRegex(
            mobile_styles,
            r"\.permission-category-nav\s*\{[^}]*position:\s*static;",
        )
        self.assertRegex(
            mobile_styles,
            r"\.access-permission-grid\s+\.permission-matrix\s+"
            r"\.permission-name-cell\s*\{[^}]*position:\s*static;",
        )
        self.assertRegex(
            stylesheet,
            r"\.permission-matrix\s+\.permission-choice-cell\s*\{[^}]*padding:\s*0;",
        )
        self.assertRegex(
            stylesheet,
            r"\.permission-choice-cell label\s*\{[^}]*width:\s*100%;"
            r"[^}]*min-width:\s*0;",
        )
        self.assertRegex(
            stylesheet,
            r"\.role-permission-choice-cell label\s*\{[^}]*min-width:\s*0;"
            r"[^}]*flex:\s*0 0 auto;",
        )
