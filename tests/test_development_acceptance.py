"""Independent acceptance checks using synthetic databases and authenticated Web requests."""
from __future__ import annotations

import sqlite3
import io
import json
import re
import tempfile
import unittest
from html import unescape
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

    def test_worksheet_status_saves_reopens_and_filters_completed_separately(self):
        self.login()
        form = {"core_code": "QA-STATUS-8461", "rows": json.dumps([{"name": "主体"}])}
        created = self.client.post("/development/worksheets", data={**form, "status": "not_started"})
        self.assertEqual(created.status_code, 302)
        detail_url = created.location
        project_id = detail_url.rsplit("/", 1)[-1]
        for state in ("not_started", "in_progress", "completed", "in_progress"):
            with self.subTest(state=state):
                if state != "not_started":
                    self.post_development(project_id, "worksheet", {**form, "status": state})
                saved = self.service().detail(project_id)
                self.assertEqual(saved["project"]["status"], state)
                self.assertIs(saved["worksheet"]["started"], state != "not_started")
                if state == "completed":
                    self.assertIs(saved["worksheet"]["completed"], True)
                else:
                    self.assertNotIn("completed", saved["worksheet"])
                reopened = self.client.get(detail_url).get_data(as_text=True)
                self.assertIn('name="status"', reopened)
                self.assertIn(f'<option value="{state}" selected', reopened)
                for requested in ("not_started", "in_progress", "completed"):
                    response = self.client.get("/development", query_string={"q": form["core_code"], "status": requested})
                    self.assertEqual(response.status_code, 200)
                    html = response.get_data(as_text=True)
                    self.assertEqual(f'href="{detail_url}"' in html, requested == state)
                    if requested == state == "completed":
                        self.assertIn("<td>已完成</td>", html)

    def test_worksheet_legacy_started_values_still_save_without_a_completion_field(self):
        self.login()
        form = {"core_code": "QA-OLD-STATUS", "rows": "[]"}
        created = self.client.post("/development/worksheets", data={**form, "started": "0"})
        self.assertEqual(created.status_code, 302)
        project_id = created.location.rsplit("/", 1)[-1]
        initial = self.service().detail(project_id)["worksheet"]
        self.assertIs(initial["started"], False)
        self.assertNotIn("completed", initial)
        for value in ("1", "0"):
            with self.subTest(started=value):
                saved = self.post_development(project_id, "worksheet", {**form, "started": value})["worksheet"]
                self.assertIs(saved["started"], value == "1")
                self.assertNotIn("completed", saved)

    def test_worksheet_completion_errors_and_view_permissions_preserve_saved_state(self):
        self.login()
        form = {"core_code": "QA-DRAFT-STATUS", "status": "in_progress", "rows": "[]"}
        created = self.client.post("/development/worksheets", data=form)
        self.assertEqual(created.status_code, 302)
        project_id = created.location.rsplit("/", 1)[-1]
        url = created.location + "/worksheet"
        saved = self.service().detail(project_id)
        pending = {**form, "status": "completed", "notes": "试装完成，等待签收",
                   "expected_version": saved["project"]["version"]}
        error = self.client.post(url, data={**pending, "rows": json.dumps([{"name": "球销", "mode": "borrowed"}])})
        self.assertEqual(error.status_code, 400)
        html = error.get_data(as_text=True)
        self.assertIn("借用编码", html)
        self.assertIn('<option value="completed" selected', html)
        self.assertIn(pending["notes"], html)
        invalid = self.client.post(url, data={**pending, "status": "unknown"})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("开发状态", invalid.get_data(as_text=True))
        self.viewer()
        readonly = self.client.get(created.location)
        self.assertEqual(readonly.status_code, 200)
        self.assertIn('<option value="in_progress" selected', readonly.get_data(as_text=True))
        denied = self.client.post(url, data=pending, headers={"Accept": "application/json"})
        self.assertEqual(denied.status_code, 403)
        after = self.service().detail(project_id)
        self.assertEqual(after["worksheet"], saved["worksheet"])
        self.assertEqual(after["project"]["version"], saved["project"]["version"])

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

    def test_core_code_sheet_saves_custom_and_cleared_materials(self):
        from app.modules.development.worksheet import default_worksheet

        self.login()
        new = self.client.get("/development/new").get_data(as_text=True)
        self.assertEqual(new.count('value="主体"'), 1)
        parts = default_worksheet()["rows"]
        next(row for row in parts if row["name"] == "主体")["code"] = "BT8461"
        next(row for row in parts if row["name"] == "球壳")["material"] = "客户指定 42CrMo"
        next(row for row in parts if row["name"] == "防尘罩")["material"] = ""
        created = self.client.post("/development/worksheets", data={
            "core_code": "8461", "started": "1", "rows": json.dumps(parts, ensure_ascii=False),
        })
        self.assertEqual(created.status_code, 302)
        detail = self.client.get(created.location).get_data(as_text=True)
        self.assertIn('name="core_code" value="8461"', detail)
        self.assertEqual(detail.count('value="主体"'), 1)
        self.assertIn('value="BT8461"', detail)
        saved = self.service().detail(created.location.rstrip("/").split("/")[-1])["worksheet"]["rows"]
        self.assertEqual({row["name"]: row["material"] for row in saved if row["name"] in {"球壳", "防尘罩"}},
                         {"球壳": "客户指定 42CrMo", "防尘罩": ""})
        self.assertRegex(detail, r'<input\b(?=[^>]*data-field="material")(?=[^>]*value="客户指定 42CrMo")[^>]*>')
        for field in ('name="left_code"', 'name="right_code"', 'data-field="side"', "球头子件"):
            self.assertNotIn(field, detail)
        results = self.client.get("/development?q=8461").get_data(as_text=True)
        self.assertIn(f'href="{created.location}"', results)
        self.assertIn("核心编码", results)

    def test_unknown_uuid_does_not_disclose_internal_path(self):
        self.login()
        response = self.client.get(f"/development/{uuid4()}")
        self.assertIn(response.status_code, (400, 404))
        text = response.get_data(as_text=True)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("Traceback", text)

    def test_worksheet_rejects_unreadable_rows_without_offering_an_empty_replacement(self):
        from app.modules.development.worksheet import default_worksheet

        self.login()
        sheet = {**default_worksheet(), "core_code": "QA-DRAFT"}
        saved = self.service().create_worksheet(sheet, actor="007")["project"]
        url = f"/development/{saved['id']}/worksheet"
        data = {"core_code": "QA-DRAFT", "expected_version": saved["version"]}
        for raw in ("{", "[null]", '[{"name":{"nested":true}}]', " " * 256_001):
            with self.subTest(size=len(raw)):
                response = self.client.post(url, data={**data, "rows": raw})
                self.assertEqual(response.status_code, 400)
                html = response.get_data(as_text=True)
                self.assertIn("打开已保存的开发表", html)
                self.assertNotIn("data-sheet-save", html)
                self.assertNotIn('name="rows"', html)
        rows = [{**sheet["rows"][0], "code": f"PART-{i}"} for i in range(501)]
        response = self.client.post(url, data={**data, "rows": json.dumps(rows, ensure_ascii=False)})
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("500 行", html)
        self.assertIn("data-sheet-save", html)
        payload = re.search(r'name="rows" value="([^"]*)"', html)
        self.assertIsNotNone(payload)
        retained = json.loads(unescape(payload[1]))
        self.assertEqual(retained, rows)
        self.assertEqual(self.service().detail(saved["id"])["worksheet"], sheet)
        response = self.client.post(url, data={**data, "rows": json.dumps(retained[:-1], ensure_ascii=False)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.service().detail(saved["id"])["worksheet"]["rows"], rows[:-1])

    def test_whole_sheet_notes_survive_older_forms_and_can_be_edited_or_cleared(self):
        self.login()
        old_parts = [{"name": "臂体", "material": "铸铝"}, {"name": "衬套 1", "material": ""},
                     {"name": "球销", "material": ""}]
        data = {"core_code": "8461", "rows": json.dumps(old_parts, ensure_ascii=False)}
        created = self.client.post("/development/worksheets", data={**data, "notes": "样件已到\r\n等待试装"})
        self.assertEqual(created.status_code, 302)
        detail_url = created.location
        self.assertIn("样件已到\n等待试装</textarea>", self.client.get(detail_url).get_data(as_text=True))

        def save(version, **changes):
            return self.client.post(detail_url + "/worksheet", data={**data, "expected_version": version, **changes})

        self.assertEqual(save(1).status_code, 302)
        self.assertIn("样件已到\n等待试装</textarea>", self.client.get(detail_url).get_data(as_text=True))
        self.assertEqual(save(2, notes="已试装").status_code, 302)
        stale = save(2, notes="仍在等待")
        self.assertEqual(stale.status_code, 409)
        self.assertIn("仍在等待</textarea>", stale.get_data(as_text=True))
        self.assertIn("已试装</textarea>", self.client.get(detail_url).get_data(as_text=True))
        self.assertEqual(save(3, notes="").status_code, 302)
        self.assertNotIn("已试装", self.client.get(detail_url).get_data(as_text=True))
        retained = self.service().detail(detail_url.rstrip("/").split("/")[-1])["worksheet"]["rows"]
        self.assertEqual([(row["name"], row["material"]) for row in retained],
                         [(row["name"], row["material"]) for row in old_parts])

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
