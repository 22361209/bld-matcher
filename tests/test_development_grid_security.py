"""Adversarial BOM-grid checks. Every database and file is synthetic."""
from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.web_app_test_base import WebAppTestBase


class DevelopmentGridServiceSecurityTest(unittest.TestCase):
    def setUp(self):
        from app.modules.development.document_schema import create_schema
        from app.modules.development.repository import SQLiteDevelopmentUnitOfWork
        from app.modules.development.schema import install_schema
        from app.modules.development.service import DevelopmentService

        self.temporary = tempfile.TemporaryDirectory(prefix="bld-grid-security-")
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "synthetic.sqlite3"
        with sqlite3.connect(self.database) as connection:
            connection.execute("""CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY, action TEXT, target_type TEXT, target_key TEXT,
                detail TEXT, actor TEXT, created_at TEXT)""")
            install_schema(connection)
            create_schema(connection)
        self.factory = lambda: SQLiteDevelopmentUnitOfWork(self.database)
        self.service = DevelopmentService(self.factory)
        self.state, self.branch_id = self.project("Primary synthetic project", "8461")
        self.project_id = self.state["project"]["id"]

    def project(self, title, core_number):
        state = self.service.create_project({"title": title, "core_number": core_number,
                                             "owner_username": "owner"}, actor="owner")
        state = self.service.add_branch(state["project"]["id"], {
            "code": f"K{core_number}LA", "owner_username": "owner", "planned_date": "2026-11-01",
            "drawing_required": False, "drawing_reason": "Synthetic fixture",
        }, expected_version=state["project"]["version"], actor="owner")
        return state, state["branches"][0]["id"]

    def add_component(self, *, branch_id=None, task_id=None, borrowed_code=""):
        data = {"name": "Synthetic component", "borrowed_code": borrowed_code}
        if task_id:
            data["task_id"] = task_id
        else:
            data["task"] = {"drawing_required": False, "drawing_reason": "Synthetic fixture"}
        before_ids = {row["id"] for row in self.state["components"]}
        self.state = self.service.add_component(
            self.project_id, branch_id or self.branch_id, data,
            expected_version=self.state["project"]["version"], actor="owner",
        )
        return next(row for row in self.state["components"] if row["id"] not in before_ids)

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {table: sorted(connection.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
                    for table in tables}

    def save(self, rows, *, expected_version=None, branch_id=None):
        result = self.service.save_component_grid(
            self.project_id, branch_id or self.branch_id, rows,
            expected_version=expected_version if expected_version is not None else self.state["project"]["version"],
            actor="owner",
        )
        self.state = result["detail"]
        return result

    def assert_rejected_unchanged(self, rows, *, branch_id=None):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.save(rows, branch_id=branch_id)
        self.assertEqual(self.snapshot(), before)

    def test_foreign_branch_or_parent_uuid_cannot_redirect_batch(self):
        foreign, foreign_branch = self.project("Foreign synthetic project", "9123")
        foreign = self.service.add_component(foreign["project"]["id"], foreign_branch, {
            "name": "Foreign component", "task": {"drawing_required": False, "drawing_reason": "Synthetic"},
        }, expected_version=foreign["project"]["version"], actor="owner")
        self.assert_rejected_unchanged([{"client_id": "first", "name": "Attempt"}], branch_id=foreign_branch)
        self.assert_rejected_unchanged([{"client_id": "first", "name": "Attempt",
                                        "parent_id": foreign["components"][0]["id"]}])
        self.assert_rejected_unchanged([{"id": foreign["components"][0]["id"], "name": "Attempt"}])

    def test_parent_from_another_branch_of_the_same_project_is_rejected(self):
        parent = self.add_component()
        self.state = self.service.add_branch(self.project_id, {"code": "K8461RA"},
            expected_version=self.state["project"]["version"], actor="owner")
        other_branch = next(row["id"] for row in self.state["branches"] if row["id"] != self.branch_id)
        self.assert_rejected_unchanged([{"client_id": "first", "name": "Attempt", "parent_id": parent["id"]}],
                                       branch_id=other_branch)

    def test_new_temporary_parent_cycle_rolls_back_every_row(self):
        self.assert_rejected_unchanged([
            {"client_id": "first", "name": "First", "parent_client_id": "second"},
            {"client_id": "second", "name": "Second", "parent_client_id": "first"},
        ])

    def test_existing_tree_cycle_rolls_back_other_valid_edits(self):
        first, second = self.add_component(), self.add_component()
        self.assert_rejected_unchanged([
            {"id": first["id"], "name": "Unsaved changed value", "parent_id": second["id"]},
            {"id": second["id"], "parent_id": first["id"]},
        ])

    def test_unrecognized_or_mismatched_borrowed_source_is_rejected(self):
        component = self.add_component(borrowed_code="SYNTHETIC-SOURCE-A")
        self.assert_rejected_unchanged([{"client_id": "new", "name": "Attempt",
                                        "borrowed_source_id": str(uuid4())}])
        self.assert_rejected_unchanged([{"client_id": "new", "name": "Attempt",
                                        "borrowed_source_id": component["borrowed_source_id"],
                                        "borrowed_code": "SYNTHETIC-SOURCE-B"}])

    def test_identity_and_audit_fields_cannot_be_mass_assigned(self):
        for field in ("actor", "created_by", "project_id", "branch_id", "task_id", "archived", "version"):
            with self.subTest(field=field):
                self.assert_rejected_unchanged([{"client_id": "new", "name": "Attempt", field: str(uuid4())}])
        self.assert_rejected_unchanged([{"client_id": "new", "name": "Attempt", "task": {"created_by": "forged"}}])

    def test_same_shared_task_cannot_receive_conflicting_values(self):
        first = self.add_component()
        second = self.add_component(task_id=first["task_id"])
        self.assert_rejected_unchanged([
            {"id": first["id"], "task": {"planned_date": "2026-11-01"}},
            {"id": second["id"], "task": {"planned_date": "2026-11-02"}},
        ])

    def test_duplicate_ids_and_ambiguous_parents_are_rejected(self):
        existing = self.add_component()
        for rows in ([{"client_id": "same", "name": "One"}, {"client_id": "same", "name": "Two"}],
                     [{"id": existing["id"], "name": "One"}, {"id": existing["id"], "name": "Two"}],
                     [{"client_id": "new", "name": "One", "id": existing["id"]}],
                     [{"client_id": "new", "name": "One", "parent_id": existing["id"], "parent_client_id": "another"}]):
            with self.subTest(rows=rows):
                self.assert_rejected_unchanged(rows)

    def test_late_invalid_row_preserves_database_and_caller_input(self):
        rows = [{"client_id": "first", "name": "Keep my entered name", "borrowed_code": "NEW-SOURCE"},
                {"client_id": "invalid", "name": "Invalid", "quantity": -1}]
        submitted = copy.deepcopy(rows)
        self.assert_rejected_unchanged(rows)
        self.assertEqual(rows, submitted)

    def test_stale_version_rejects_batch_without_any_partial_write(self):
        old_version = self.state["project"]["version"]
        self.state = self.service.update_project(self.project_id, {"notes": "Concurrent change"},
                                                expected_version=old_version, actor="owner")
        before = self.snapshot()
        with self.assertRaises(ValueError) as caught:
            self.save([{"client_id": "new", "name": "Stale edit"}], expected_version=old_version)
        self.assertEqual(getattr(caught.exception, "code", ""), "development.version_conflict")
        self.assertEqual(self.snapshot(), before)

    def test_audit_failure_rolls_back_components_tasks_sources_and_version(self):
        with self.factory() as unit:
            repository_type = type(unit.repository)
        before = self.snapshot()
        with patch.object(repository_type, "audit", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                self.save([{"client_id": "new", "name": "Atomic component", "borrowed_code": "NEW-SOURCE"}])
        self.assertEqual(self.snapshot(), before)

    def test_new_supplier_is_atomic_and_requires_explicit_boolean_consent(self):
        self.assert_rejected_unchanged([
            {"client_id": "first", "name": "Valid first row", "task": {
                "supplier_name": "Synthetic new supplier", "create_supplier": True}},
            {"client_id": "invalid", "name": "Invalid final row", "quantity": -1},
        ])
        for consent in ("true", "1", 1, None):
            with self.subTest(consent=consent):
                self.assert_rejected_unchanged([{"client_id": "first", "name": "Attempt", "task": {
                    "supplier_name": "Synthetic new supplier", "create_supplier": consent}}])

    def test_shared_task_change_invalidates_owner_confirmations_on_both_branches(self):
        component = self.add_component()
        self.state = self.service.update_task(self.project_id, component["task_id"], {
            "owner_username": "owner", "status": "completed", "actual_date": "2026-10-01",
        }, expected_version=self.state["project"]["version"], actor="owner")
        self.state = self.service.copy_branch(self.project_id, self.branch_id, {"code": "K8461RA"},
            modes={component["id"]: "shared"}, expected_version=self.state["project"]["version"], actor="owner")
        for branch in list(self.state["branches"]):
            self.state = self.service.update_branch(self.project_id, branch["id"], {"production_ready": 1},
                expected_version=self.state["project"]["version"], actor="owner")
            self.state = self.service.confirm_branch(self.project_id, branch["id"],
                expected_version=self.state["project"]["version"], actor="owner")
        self.assertTrue(all(branch["confirmed_by"] for branch in self.state["branches"]))
        self.save([{"id": component["id"], "task": {"planned_date": "2026-12-01"}}])
        self.assertTrue(all(not branch["confirmed_by"] for branch in self.state["branches"]))
        self.assertTrue(all(not branch["actual_date"] for branch in self.state["branches"]))


class DevelopmentGridWebSecurityTest(WebAppTestBase):
    def setUp(self):
        from app.modules.development.factory import get_development_service

        self.client = self.web.app.test_client()
        self.web.app.config["TESTING"] = True
        self.service = get_development_service()
        self.state = self.service.create_project({"core_number": "8461", "title": "Synthetic grid security",
                                                  "owner_username": "007"}, actor="007")
        self.project_id = self.state["project"]["id"]
        self.state = self.service.add_branch(self.project_id, {"code": "K8461LA", "owner_username": "007"},
            expected_version=self.state["project"]["version"], actor="007")
        self.branch_id = self.state["branches"][0]["id"]
        self.url = f"/development/{self.project_id}/branches/{self.branch_id}/grid/save"

    def snapshot(self):
        with sqlite3.connect(self.web.DB_PATH) as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'development_%' OR name='audit_logs')")]
            return {table: sorted(connection.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
                    for table in tables}

    def payload(self):
        return {"expected_version": self.state["project"]["version"],
                "rows": [{"client_id": "synthetic-new", "name": "Preserve this entered name"}]}

    def post(self, payload=None, **kwargs):
        return self.client.post(self.url, json=self.payload() if payload is None else payload,
                                headers={"Accept": "application/json", **kwargs.pop("headers", {})}, **kwargs)

    def restricted_user(self, permissions):
        from app.modules.admin.factory import get_admin_service

        user_id = get_admin_service().save_user({
            "username": f"grid-security-{uuid4().hex[:10]}", "display_name": "Synthetic grid role",
            "password": "synthetic-grid-password", "role": "viewer", "active": "1",
            "permission_overrides": permissions,
        }, actor="007")
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def test_anonymous_and_read_only_users_cannot_save_grid(self):
        before = self.snapshot()
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.snapshot(), before)
        self.restricted_user({"view_development": "allow", "edit_development": "deny"})
        before = self.snapshot()
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_json_grid_write_requires_csrf(self):
        self.login()
        before = self.snapshot()
        self.web.app.config["TESTING"] = False
        try:
            response = self.post()
        finally:
            self.web.app.config["TESTING"] = True
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_valid_csrf_header_and_trusted_actor_are_used(self):
        self.login()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "synthetic-grid-csrf"
        self.web.app.config["TESTING"] = False
        try:
            response = self.post(headers={"X-CSRF-Token": "synthetic-grid-csrf"})
        finally:
            self.web.app.config["TESTING"] = True
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["ok"])
        created_id = body["client_ids"]["synthetic-new"]
        with sqlite3.connect(self.web.DB_PATH) as connection:
            actor = connection.execute("SELECT created_by FROM development_components WHERE id=?", (created_id,)).fetchone()[0]
            audit_actor = connection.execute("SELECT actor FROM audit_logs WHERE target_key=? ORDER BY id DESC LIMIT 1",
                                             (self.project_id,)).fetchone()[0]
        self.assertEqual(actor, "007")
        self.assertEqual(audit_actor, "007")

    def test_top_level_actor_and_unknown_fields_are_rejected_without_writes(self):
        self.login()
        for extra in ({"actor": "forged-administrator"}, {"project_id": str(uuid4())}, {"unrecognized": True}):
            with self.subTest(extra=extra):
                before = self.snapshot()
                response = self.post({**self.payload(), **extra})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(), before)

    def test_stale_grid_submission_returns_409_and_keeps_first_write(self):
        self.login()
        payload = self.payload()
        first = self.post(payload)
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        before = self.snapshot()
        payload["rows"][0]["name"] = "Keep this second-tab draft"
        response = self.post(payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "development.version_conflict")
        self.assertEqual(self.snapshot(), before)

    def test_invalid_last_row_returns_safe_error_and_rolls_back_batch(self):
        self.login()
        payload = self.payload()
        payload["rows"].append({"client_id": "invalid", "name": "Invalid final row", "quantity": -1})
        before = self.snapshot()
        response = self.post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)
        text = response.get_data(as_text=True)
        for internal in (str(self.web.DB_PATH), "Traceback", "INSERT INTO", "UPDATE development"):
            self.assertNotIn(internal, text)

    def test_server_failure_keeps_all_rows_and_hides_internal_details(self):
        self.login()
        with self.service.unit_of_work_factory() as unit:
            repository_type = type(unit.repository)
        before = self.snapshot()
        with patch.object(repository_type, "audit", side_effect=RuntimeError("synthetic internal SQL /secret/path")):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(response.get_json()["ok"])
        self.assertNotIn("synthetic internal", response.get_data(as_text=True))
        self.assertNotIn("/secret/path", response.get_data(as_text=True))

    def lookup_component(self, *, borrowed_code="SYNTHETIC-GRID-SOURCE"):
        self.state = self.service.add_component(self.project_id, self.branch_id, {
            "name": "Synthetic lookup component", "borrowed_code": borrowed_code,
            "task": {"notes": "Synthetic authorized development note"},
        }, expected_version=self.state["project"]["version"], actor="007")
        return self.state["components"][-1]

    def test_source_lookup_requires_development_permission(self):
        self.lookup_component()
        url = f"/development/{self.project_id}/lookup/sources?q=SYNTHETIC-GRID"
        self.assertEqual(self.client.get(url, headers={"Accept": "application/json"}).status_code, 401)
        self.restricted_user({"view_development": "deny", "edit_development": "allow"})
        self.assertEqual(self.client.get(url, headers={"Accept": "application/json"}).status_code, 403)

    def test_source_lookup_exposes_only_development_fields_without_file_or_product_permission(self):
        component = self.lookup_component()
        self.restricted_user({"view_development": "allow", "view_development_files": "deny",
                              "view_products": "deny", "view_product_drawings": "deny",
                              "view_material_drawings": "deny"})
        response = self.client.get(f"/development/{self.project_id}/lookup/sources", query_string={
            "q": component["borrowed_code"], "component_id": component["id"], "source_type": "products",
        }, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        candidate = next(row for row in body["candidates"] if row["id"] == component["borrowed_source_id"])
        self.assertEqual(set(candidate), {"id", "code", "name", "task_suggestions", "suggestions_truncated"})
        for suggestion in candidate["task_suggestions"]:
            self.assertEqual(set(suggestion["fields"]), {
                "name", "procurement_mode", "supplier_id", "material_category", "material_grade",
                "forming_process", "notes", "development_mode",
            })
        text = response.get_data(as_text=True)
        for field in ("storage_path", "price_cny", "tax_price", "password_hash", "download_url", "revision_id"):
            self.assertNotIn(field, text)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_component_document_context_requires_independent_file_permission(self):
        component = self.lookup_component()
        self.restricted_user({"view_development": "allow", "view_development_files": "deny"})
        url = f"/development/{self.project_id}/lookup/components/{component['id']}"
        response = self.client.get(url, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(component["name"], response.get_data(as_text=True))

    def test_lookup_component_uuid_cannot_cross_projects(self):
        self.login()
        foreign = self.service.create_project({"core_number": "9123", "title": "Foreign synthetic lookup"}, actor="007")
        foreign_id = foreign["project"]["id"]
        foreign = self.service.add_branch(foreign_id, {"code": "K9123LA"},
            expected_version=foreign["project"]["version"], actor="007")
        foreign = self.service.add_component(foreign_id, foreign["branches"][0]["id"], {"name": "Foreign confidential component"},
            expected_version=foreign["project"]["version"], actor="007")
        foreign_component = foreign["components"][0]["id"]
        for url in (f"/development/{self.project_id}/lookup/components/{foreign_component}",
                    f"/development/{self.project_id}/lookup/sources?component_id={foreign_component}"):
            with self.subTest(url=url):
                response = self.client.get(url, headers={"Accept": "application/json"})
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Foreign confidential", response.get_data(as_text=True))

    def test_lookup_query_bounds_reject_invalid_and_treat_wildcards_literally(self):
        self.login()
        self.lookup_component()
        url = f"/development/{self.project_id}/lookup/sources"
        for query in ({"limit": "0"}, {"limit": "51"}, {"limit": "true"}, {"q": "x" * 101}, {"q": "bad\nquery"}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(url, query_string=query).status_code, 400)
        response = self.client.get(url, query_string={"q": "%"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["candidates"], [])


if __name__ == "__main__":
    unittest.main()
