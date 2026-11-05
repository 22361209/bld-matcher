from __future__ import annotations

import io
import subprocess
import sys
from unittest.mock import patch
from uuid import uuid4

from werkzeug.datastructures import FileStorage

from tests.web_app_test_base import WebAppTestBase


PDF = b"%PDF-1.4\nsynthetic drawing\n%%EOF\n"


class LegacyDevelopmentDeletionTest(WebAppTestBase):
    def setUp(self):
        from app.modules.development.document_factory import get_development_document_service
        from app.modules.development.factory import get_development_service

        self.client = self.web.app.test_client()
        self.login()
        self.core = get_development_service()
        self.files = get_development_document_service()
        self.project = self.legacy_project("待删除的旧单")

    def legacy_project(self, title):
        return self.core.create_project({"core_number": "8461", "title": title}, actor="007")["project"]

    def post_delete(self, project=None, **form):
        project = project or self.project
        return self.client.post(f"/development/{project['id']}/delete-legacy",
                                headers={"X-Requested-With": "fetch"},
                                data={"confirmed_title": project["title"], "expected_version": project["version"], **form})

    def attach_drawing(self, project, *, task_id=None):
        project_id = project["id"]
        target = {"target_id": task_id, "target_type": "task"} if task_id else {}
        return self.files.upload(project_id, {"kind": "drawing", "number": "X1", "title": "合成图纸", "label": "A",
                                             "change_note": "测试", "state": "released", **target},
                                 FileStorage(stream=io.BytesIO(PDF), filename="X1.pdf", content_type="application/pdf"),
                                 expected_version=project["version"], actor="007")

    def add_component(self, project, supplier_id, source_id=None):
        project_id = project["id"]
        branch = self.core.add_branch(project_id, {"code": "K8461LA", "side": "left"},
                                      expected_version=project["version"], actor="007")
        return self.core.add_component(project_id, branch["branches"][0]["id"], {
            "name": "球销", "code": "QD8461", "borrowed_code": "QD8070", "borrowed_source_id": source_id,
            "task": {"supplier_id": supplier_id},
        }, expected_version=branch["project"]["version"], actor="007")

    def test_exact_title_current_version_and_legacy_type_required(self):
        from app.modules.development.worksheet import default_worksheet

        self.assertEqual(self.post_delete(confirmed_title="待删除").status_code, 400)
        self.assertEqual(self.post_delete(expected_version="0").status_code, 409)
        self.assertEqual(self.core.detail(self.project["id"])["project"], self.project)
        sheet = self.core.create_worksheet({**default_worksheet(), "core_code": "8461"}, actor="007")
        self.assertEqual(self.post_delete(sheet["project"]).status_code, 400)
        self.assertEqual(self.core.detail(sheet["project"]["id"])["worksheet"], sheet["worksheet"])

    def test_viewer_cannot_delete_and_csrf_is_enforced_for_authenticated_editor(self):
        from app.modules.admin.factory import get_admin_service

        with patch.dict(self.web.app.config, {"TESTING": False}):
            self.assertEqual(self.post_delete().status_code, 400)
        user_id = get_admin_service().save_user({
            "username": f"deletion-reader-{uuid4().hex[:8]}", "password": "test-password", "role": "viewer",
            "active": "1", "permission_overrides": {"view_development": "allow", "edit_development": "deny"},
        }, actor="007")
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
        self.assertEqual(self.post_delete().status_code, 403)
        self.assertEqual(self.core.detail(self.project["id"])["project"], self.project)

    def test_removes_only_selected_legacy_project_and_retires_its_drawing(self):
        identifier = self.project["id"]
        supplier = self.core.save_supplier({"name": "合成供应商"}, actor="007")
        current = self.add_component(self.project, supplier["id"])
        source_id = current["components"][0]["borrowed_source_id"]
        task_id = current["tasks"][0]["id"]
        current = self.core.add_operation(identifier, task_id, {"sequence": "1", "name": "试制"},
                                          expected_version=current["project"]["version"], actor="007")
        drawing = self.attach_drawing(current["project"], task_id=task_id)
        original = self.files.file_payload(identifier, drawing["id"]).path
        other = self.add_component(self.legacy_project("保留的旧单"), supplier["id"], source_id)
        other_id = other["project"]["id"]
        other_drawing = self.attach_drawing(other["project"])
        before = self.core.detail(other_id)
        response = self.post_delete(self.core.detail(identifier)["project"])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(f"/development/{identifier}").status_code, 404)
        self.assertEqual(self.client.get(f"/development/{identifier}/documents/{drawing['id']}/download").status_code, 404)
        self.assertFalse(original.exists())
        archived, = (self.files.storage.root / ".deleted").glob(f"{identifier}-*/*/*")
        self.assertEqual(archived.read_bytes(), PDF)
        self.assertEqual(self.files.file_payload(other_id, other_drawing["id"]).path.read_bytes(), PDF)
        self.assertEqual(self.core.detail(other_id), before)
        self.assertIn(supplier["id"], {row["id"] for row in self.core.list_suppliers()})
        self.assertIn(source_id, {row["id"] for row in self.core.list_sources()})
        with self.web.connect(self.web.DB_PATH) as connection:
            for table in ("branches", "tasks", "components", "operations", "documents", "document_links", "document_revisions"):
                with self.subTest(table=table):
                    self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM development_{table} WHERE project_id=?",
                                                        (identifier,)).fetchone()[0], 0)
            audit = connection.execute("SELECT actor, detail FROM audit_logs WHERE action='删除旧开发单' AND target_key=?",
                                       (identifier,)).fetchone()
        self.assertEqual(tuple(audit), ("007", self.project["title"]))

    def test_database_commit_failure_restores_the_live_file_and_legacy_record(self):
        identifier = self.project["id"]
        drawing = self.attach_drawing(self.project)
        before = self.core.detail(identifier)["project"]
        with patch("app.modules.development.repository.SQLiteDevelopmentUnitOfWork.commit",
                   side_effect=OSError("synthetic commit failure")), self.assertRaisesRegex(OSError, "synthetic commit"):
            self.files.delete_legacy_project(identifier, expected_version=before["version"],
                                             confirmed_title=before["title"], actor="007")
        self.assertEqual(self.core.detail(identifier)["project"], before)
        self.assertEqual(self.files.file_payload(identifier, drawing["id"]).path.read_bytes(), PDF)
        self.assertFalse(list((self.files.storage.root / ".deleted").glob(f"{identifier}-*")))

    def test_worker_exit_before_or_after_commit_preserves_attachments(self):
        worker = """
import os, sys
from app.modules.development.document_factory import get_development_document_service
from app.modules.development.repository import SQLiteDevelopmentUnitOfWork
original = SQLiteDevelopmentUnitOfWork.commit
def interrupt(work):
    if sys.argv[3] == 'after':
        original(work)
    os._exit(79)
SQLiteDevelopmentUnitOfWork.commit = interrupt
get_development_document_service().delete_legacy_project(sys.argv[1], expected_version=2,
                                                        confirmed_title=sys.argv[2], actor='QA')
"""
        for phase in ("before", "after"):
            with self.subTest(phase=phase):
                project = self.legacy_project("QA crash " + phase)
                identifier = project["id"]
                revision = self.attach_drawing(project)
                response = subprocess.run([sys.executable, "-c", worker, identifier, project["title"], phase],
                                          timeout=15, check=False)
                self.assertEqual(response.returncode, 79)
                download = self.client.get(f"/development/{identifier}/documents/{revision['id']}/download")
                if phase == "before":
                    self.assertEqual(download.status_code, 200)
                    self.assertEqual(download.get_data(), PDF)
                else:
                    self.assertEqual(download.status_code, 404)
                    archived, = (self.files.storage.root / ".deleted").glob(f"{identifier}-*/*/*")
                    self.assertEqual(archived.read_bytes(), PDF)
                download.close()

    def test_reimport_between_delete_commit_and_cleanup_keeps_new_live_files(self):
        from app.modules.development.repository import SQLiteDevelopmentUnitOfWork

        identifier = self.project["id"]
        old_file = self.attach_drawing(self.project)
        with self.core.unit_of_work_factory() as work:
            aggregate = work.repository.export_aggregate(identifier)
        commit = SQLiteDevelopmentUnitOfWork.commit
        restored = []

        def commit_then_restore(work):
            commit(work)
            if restored:
                return
            restored.append(identifier)
            with self.core.unit_of_work_factory() as restore:
                restore.repository.import_aggregate(aggregate)
                commit(restore)
            restored.append(self.attach_drawing(aggregate["project"])["id"])

        with patch.object(SQLiteDevelopmentUnitOfWork, "commit", commit_then_restore):
            self.files.delete_legacy_project(identifier, expected_version=2, confirmed_title=self.project["title"], actor="007")
        self.assertEqual(self.files.file_payload(identifier, restored[1]).path.read_bytes(), PDF)
        archived, = (self.files.storage.root / ".deleted").glob(f"{identifier}-*/*/*")
        self.assertEqual((archived.parent.name, archived.read_bytes()), (old_file["id"], PDF))

    def test_listing_uses_one_snapshot_across_a_concurrent_delete(self):
        from app.modules.development.repository import SQLiteDevelopmentRepository

        rows = SQLiteDevelopmentRepository.rows
        def rows_then_delete(repository, kind, **params):
            result = rows(repository, kind, **params)
            if kind == "projects":
                self.files.delete_legacy_project(self.project["id"], expected_version=1,
                                                 confirmed_title=self.project["title"], actor="007")
            return result

        with patch.object(SQLiteDevelopmentRepository, "rows", rows_then_delete):
            response = self.client.get("/development?type=legacy")
        self.assertEqual(response.status_code, 200)
        link = f'/development/{self.project["id"]}'
        self.assertIn(link, response.get_data(as_text=True))
        self.assertNotIn(link, self.client.get("/development?type=legacy").get_data(as_text=True))

    def test_cleanup_error_after_commit_keeps_success_and_verified_archive(self):
        identifier = self.project["id"]
        self.attach_drawing(self.project)
        with patch.object(self.files.storage, "remove_project_files", side_effect=OSError("synthetic cleanup failure")), \
                self.assertLogs("app.modules.development.document_service", level="ERROR"):
            response = self.post_delete(expected_version=2)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(f"/development/{identifier}").status_code, 404)
        archived, = (self.files.storage.root / ".deleted").glob(f"{identifier}-*/*/*")
        self.assertEqual(archived.read_bytes(), PDF)
