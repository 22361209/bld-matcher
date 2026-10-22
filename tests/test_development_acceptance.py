"""Independent acceptance checks using synthetic databases and authenticated Web requests."""
from __future__ import annotations

import sqlite3
import io
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from tests.web_app_test_base import WebAppTestBase


FIXTURES = Path(__file__).parent / "fixtures" / "historical"
DEVELOPMENT_TABLES = (
    "development_projects", "development_branches", "development_tasks",
    "development_components", "development_operations", "development_suppliers", "development_sources",
    "development_documents", "development_document_revisions", "development_document_links",
)


class TestDevelopmentMigrationAcceptance(unittest.TestCase):
    def test_historical_upgrade_is_additive_and_idempotent(self):
        from app.database import connect

        with tempfile.TemporaryDirectory() as temporary:
            for historical in sorted(FIXTURES.glob("*.sql")):
                with self.subTest(fixture=historical.name):
                    database_path = Path(temporary) / f"{historical.stem}.sqlite3"
                    with sqlite3.connect(database_path) as connection:
                        connection.executescript(historical.read_text(encoding="utf-8"))
                        existing_tables = {
                            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        }
                        existing_products = (
                            connection.execute("SELECT bld_no FROM products ORDER BY bld_no").fetchall()
                            if "products" in existing_tables else []
                        )
                    for _ in range(2):
                        with connect(database_path) as connection:
                            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                            self.assertFalse(connection.execute("PRAGMA foreign_key_check").fetchall())
                            tables = {
                                row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                            }
                            self.assertTrue(existing_tables - {"shipment_recognition_jobs", "shipment_recognition_files"} <= tables)
                            self.assertEqual(
                                [tuple(row) for row in connection.execute("SELECT bld_no FROM products ORDER BY bld_no")],
                                existing_products,
                            )
                            self.assertTrue(set(DEVELOPMENT_TABLES) <= tables)
                            for table in DEVELOPMENT_TABLES:
                                columns = {row[1]: row[2] for row in connection.execute(f"PRAGMA table_info({table})")}
                                self.assertEqual(columns["id"], "TEXT")
                                foreign_tables = {row[2] for row in connection.execute(f"PRAGMA foreign_key_list({table})")}
                                self.assertNotIn("materials", foreign_tables)


class TestDevelopmentWebAcceptance(WebAppTestBase):
    def setUp(self):
        self.client = self.web.app.test_client()
        self.web.app.config["TESTING"] = True

    def service(self):
        from app.modules.development.factory import get_development_service

        return get_development_service()

    def create_project(self, title="QA 合成开发单"):
        return self.service().create_project(
            {"core_number": "8461", "title": title, "owner_username": "007"}, actor="007",
        )

    def version(self, project_id):
        return self.service().detail(project_id)["project"]["version"]

    def post_development(self, project_id, suffix, data):
        defaults = {"drawing_required": "1", "drawing_status": "not_started"} if suffix.startswith("branches/") else {}
        response = self.client.post(
            f"/development/{project_id}/{suffix}",
            data={"expected_version": self.version(project_id), **defaults, **data},
        )
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[-1000:])
        return self.service().detail(project_id)

    def viewer(self, *, file_permission=False):
        from app.modules.admin.factory import get_admin_service

        overrides = {"view_development": "allow", "edit_development": "deny"}
        overrides["view_development_files"] = "allow" if file_permission else "deny"
        user_id = get_admin_service().save_user(
            {"username": f"qa-reader-{uuid4().hex[:8]}", "display_name": "QA 合成只读账号",
             "password": "qa-synthetic-password", "role": "viewer", "active": "1",
             "permission_overrides": overrides}, actor="007",
        )
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def test_development_requires_login(self):
        response = self.client.get("/development", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 401)

    def test_list_uses_shared_scroll_boundary_for_mobile_table(self):
        class ScrollBoundaryParser(HTMLParser):
            scroll_containers = []

            def handle_starttag(self, tag, attributes):
                attrs = dict(attributes)
                if tag == "div" and {"data-grid-scroll", "data-table-scroll"}.intersection(attrs.get("class", "").split()):
                    self.scroll_containers.append(attrs)

        self.login()
        parser = ScrollBoundaryParser()
        parser.feed(self.client.get("/development").get_data(as_text=True))
        self.assertEqual(len(parser.scroll_containers), 1)
        self.assertIn("data-grid-scroll", parser.scroll_containers[0])

    def test_read_only_user_cannot_create_or_mutate(self):
        self.viewer()
        page = self.client.get("/development")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('action="/development/create"', page.get_data(as_text=True))
        response = self.client.post(
            "/development/create", data={"core_number": "8461", "title": "Forbidden QA"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 403)

    def test_csrf_rejects_authenticated_write_without_mutation(self):
        self.login()
        self.web.app.config["TESTING"] = False
        try:
            response = self.client.post(
                "/development/create", data={"core_number": "8461", "title": "Missing CSRF QA"},
                headers={"Accept": "application/json"},
            )
        finally:
            self.web.app.config["TESTING"] = True
        self.assertEqual(response.status_code, 400)
        self.assertIn("页面已过期", response.get_json()["error"])

    def test_create_uses_authenticated_actor_and_keeps_core_number_separate(self):
        self.login()
        response = self.client.post(
            "/development/create",
            data={"core_number": "8461", "title": "QA actor boundary", "owner_username": "007",
                  "actor": "forged-user", "created_by": "forged-user", "username": "forged-user"},
        )
        self.assertEqual(response.status_code, 302)
        project_id = response.location.rstrip("/").split("/")[-1].split("?")[0]
        UUID(project_id)
        detail = self.service().detail(project_id)
        self.assertEqual(detail["project"]["created_by"], "007")
        self.assertEqual(detail["project"]["core_number"], "8461")

    def test_unknown_uuid_does_not_disclose_internal_path(self):
        self.login()
        response = self.client.get(f"/development/{uuid4()}")
        self.assertIn(response.status_code, (400, 404))
        text = response.get_data(as_text=True)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("Traceback", text)

    def test_stale_web_write_rejects_second_tab_and_preserves_first_write(self):
        self.login()
        project_id = self.create_project()["project"]["id"]
        version = self.version(project_id)
        first = self.client.post(
            f"/development/{project_id}/branches/add",
            data={"code": "K8461LA", "name": "左成品", "side": "left", "owner_username": "007",
                  "expected_version": version, "form_key": "new-branch", "drawing_required": "1", "drawing_status": "not_started"},
        )
        self.assertEqual(first.status_code, 302)
        second = self.client.post(
            f"/development/{project_id}/branches/add",
            data={"code": "K8461RA", "name": "过期页面新右成品", "side": "right", "owner_username": "007",
                  "expected_version": version, "form_key": "new-branch", "drawing_required": "1", "drawing_status": "not_started"},
        )
        self.assertEqual(second.status_code, 409)
        self.assertIn("过期页面新右成品", second.get_data(as_text=True))
        detail = self.service().detail(project_id)
        self.assertEqual([row["code"] for row in detail["branches"]], ["K8461LA"])
        self.assertEqual(detail["project"]["version"], version + 1)

    def test_file_view_permission_is_independent_of_project_view(self):
        project_id = self.create_project()["project"]["id"]
        self.viewer()
        self.assertEqual(self.client.get(f"/development/{project_id}").status_code, 200)
        response = self.client.get(f"/development/{project_id}?view=documents")
        self.assertEqual(response.status_code, 403)

    def test_validation_preserves_user_input_and_does_not_create_project(self):
        self.login()
        response = self.client.post(
            "/development/create", data={"core_number": "84", "title": "保留这条合成长名称", "owner_username": "007"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("保留这条合成长名称", response.get_data(as_text=True))
        self.assertIn('role="alert"', response.get_data(as_text=True))

    def test_archived_project_rejects_forged_write_and_keeps_history(self):
        self.login()
        project_id = self.create_project()["project"]["id"]
        response = self.client.post(
            f"/development/{project_id}/archive",
            data={"expected_version": self.version(project_id)},
        )
        self.assertEqual(response.status_code, 302)
        archived = self.service().detail(project_id)
        self.assertEqual(archived["project"]["archived"], 1)
        response = self.client.post(
            f"/development/{project_id}/branches/add",
            data={"code": "SHOULD-NOT-EXIST", "side": "single", "expected_version": archived["project"]["version"]},
        )
        self.assertEqual(response.status_code, 400)
        reloaded = self.service().detail(project_id)
        self.assertEqual(reloaded["branches"], [])
        self.assertEqual(reloaded["project"]["version"], archived["project"]["version"])

    def test_web_copy_preserves_separate_codes_and_shares_only_explicit_tasks(self):
        self.login()
        project_id = self.create_project()["project"]["id"]
        detail = self.post_development(project_id, "branches/add", {
            "code": "K8461LA", "name": "左成品", "side": "left", "owner_username": "007",
        })
        left_id = detail["branches"][0]["id"]
        for name in ("共用球销", "独立衬套"):
            detail = self.post_development(project_id, "components/add", {
                "branch": left_id, "name": name, "code": "QD8461", "borrowed_code": "QD8070", "quantity": "2",
                "task_name": name, "task_owner_username": "007", "task_status": "completed",
                "task_actual_date": "2026-10-01", "task_development_mode": "borrowed",
                "task_procurement_mode": "made", "task_drawing_required": "0",
                "task_drawing_reason": "已验收的合成库存样件", "task_drawing_status": "not_started",
            })
        originals = {row["name"]: row for row in detail["components"]}
        self.assertNotEqual(originals["共用球销"]["task_id"], originals["独立衬套"]["task_id"])
        detail = self.post_development(project_id, f"branches/{left_id}/copy", {
            "code": "K8461RA", "side": "right", "owner_username": "007",
            f"mode_{originals['共用球销']['id']}": "shared",
            f"mode_{originals['独立衬套']['id']}": "independent",
        })
        right_id = next(row["id"] for row in detail["branches"] if row["id"] != left_id)
        copies = {row["name"]: row for row in detail["components"] if row["branch_id"] == right_id}
        self.assertEqual(copies["共用球销"]["task_id"], originals["共用球销"]["task_id"])
        self.assertNotEqual(copies["独立衬套"]["task_id"], originals["独立衬套"]["task_id"])
        tasks = {row["id"]: row for row in detail["tasks"]}
        self.assertEqual(tasks[copies["独立衬套"]["task_id"]]["status"], "not_started")
        self.assertEqual(tasks[copies["独立衬套"]["task_id"]]["actual_date"], "")
        shared_task_id = originals["共用球销"]["task_id"]
        detail = self.post_development(project_id, f"tasks/{shared_task_id}/update", {
            "name": "共用球销", "owner_username": "007", "status": "in_progress",
            "development_mode": "borrowed", "procurement_mode": "made", "drawing_required": "0",
            "drawing_reason": "合成返工复核", "drawing_status": "not_started",
        })
        detail = self.post_development(project_id, f"components/{originals['共用球销']['id']}/update", {
            "name": "共用球销", "code": "QD8461-L-录入值", "borrowed_code": "QD8070", "quantity": "2",
        })
        reloaded = self.service().detail(project_id)
        shared = [row for row in reloaded["components"] if row["task_id"] == shared_task_id]
        self.assertEqual(len(shared), 2)
        self.assertEqual({row["code"] for row in shared}, {"QD8461-L-录入值", "QD8461"})
        self.assertEqual({row["borrowed_code"] for row in shared}, {"QD8070"})
        self.assertEqual(next(row for row in reloaded["tasks"] if row["id"] == shared_task_id)["status"], "in_progress")
        page = self.client.get(f"/development/{project_id}?view=components&branch={right_id}&component={copies['共用球销']['id']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("QD8070", page.get_data(as_text=True))

    def test_owner_confirmation_requires_production_conditions_and_reports_partial_completion(self):
        self.login()
        project_id = self.create_project()["project"]["id"]
        for code, side in (("K8461LA", "left"), ("K8461RA", "right")):
            detail = self.post_development(project_id, "branches/add", {
                "code": code, "side": side, "owner_username": "007", "drawing_required": "0",
                "drawing_reason": "标准合成成品免图",
            })
        branches = {row["code"]: row for row in detail["branches"]}
        left_id = branches["K8461LA"]["id"]
        self.post_development(project_id, "components/add", {
            "branch": left_id, "name": "合成臂体", "code": "BT8461", "quantity": "1",
            "task_name": "合成臂体", "task_owner_username": "007", "task_status": "completed",
            "task_actual_date": "2026-10-01", "task_development_mode": "new", "task_procurement_mode": "made",
            "task_drawing_required": "0", "task_drawing_reason": "合成标准工艺", "task_drawing_status": "not_started",
        })
        blocked = self.client.post(
            f"/development/{project_id}/branches/{left_id}/confirm",
            data={"expected_version": self.version(project_id)},
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("量产条件", blocked.get_data(as_text=True))
        self.post_development(project_id, f"branches/{left_id}/update", {
            "code": "K8461LA", "side": "left", "owner_username": "007", "production_ready": "1",
            "drawing_required": "0", "drawing_reason": "标准合成成品免图", "actual_date": "2026-10-01",
        })
        detail = self.post_development(project_id, f"branches/{left_id}/confirm", {})
        self.assertEqual(detail["project"]["status"], "partial_completed")
        left = next(row for row in detail["branches"] if row["id"] == left_id)
        self.assertEqual(left["status"], "completed")
        self.assertEqual(left["confirmed_by"], "007")
        self.assertEqual(left["actual_date"], "2026-10-01")

    def test_client_cannot_forge_another_responsible_owner_confirmation(self):
        self.login()
        project_id = self.create_project()["project"]["id"]
        detail = self.post_development(project_id, "branches/add", {
            "code": "K8461LA", "side": "left", "owner_username": "different-owner",
        })
        branch_id = detail["branches"][0]["id"]
        response = self.client.post(
            f"/development/{project_id}/branches/{branch_id}/confirm",
            data={"expected_version": self.version(project_id), "actor": "different-owner", "confirmed_by": "different-owner"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("负责人本人", response.get_data(as_text=True))
        self.assertFalse(self.service().detail(project_id)["branches"][0]["confirmed_by"])

    def test_sync_only_account_cannot_export_preview_or_apply_development(self):
        from app.modules.admin.factory import get_admin_service

        self.login()
        self.create_project("QA 同步权限秘密合成开发单")
        exported = self.client.post("/business-data-sync/export", data={"dataset": "development"})
        self.assertEqual(exported.status_code, 200)
        package = exported.data
        exported.close()
        user_id = get_admin_service().save_user({
            "username": f"qa-sync-{uuid4().hex[:8]}", "display_name": "QA 同步权限账号",
            "password": "qa-synthetic-password", "role": "viewer", "active": "1",
            "permission_overrides": {"sync_product_data": "allow", "view_development": "deny",
                                     "edit_development": "deny", "view_development_files": "deny"},
        }, actor="007")
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
        legacy = self.client.post("/business-data-sync/export", data={"dataset": "customers"})
        self.assertEqual(legacy.status_code, 200)
        legacy.close()
        denied = self.client.post("/business-data-sync/export", data={"dataset": "development"})
        self.assertEqual(denied.status_code, 403)
        denied = self.client.post("/business-data-sync/preview", data={"package": (io.BytesIO(package), "qa.tar.gz")})
        self.assertEqual(denied.status_code, 403)
        uploaded = max((self.root / "uploads").rglob("*.tar.gz"), key=lambda path: path.stat().st_mtime_ns)
        denied = self.client.post("/business-data-sync/apply", data={"package_path": str(uploaded), "preview_token": "unused"})
        self.assertEqual(denied.status_code, 403)
        # A package may change after route-level prechecking. Trusted service permissions must still reject it.
        with patch("app.modules.business_sync.web._authorize_package", return_value=None):
            rejected = self.client.post(
                "/business-data-sync/preview", data={"package": (io.BytesIO(package), "qa-race.tar.gz")}, follow_redirects=True,
            )
            self.assertEqual(rejected.status_code, 200)
            self.assertIn("没有开发资料", rejected.get_data(as_text=True))
            self.assertNotIn("QA 同步权限秘密合成开发单", rejected.get_data(as_text=True))
            rejected = self.client.post(
                "/business-data-sync/apply", data={"package_path": str(uploaded), "preview_token": "unused"}, follow_redirects=True,
            )
            self.assertIn("没有开发资料", rejected.get_data(as_text=True))
