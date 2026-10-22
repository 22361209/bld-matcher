"""HTML and document-adapter regression checks against isolated synthetic data."""
from __future__ import annotations

import io
import html as html_parser
import json
import re
from unittest.mock import patch
from uuid import uuid4

from tests.web_app_test_base import WebAppTestBase


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


class DevelopmentHtmlTest(WebAppTestBase):
    def setUp(self):
        self.client = self.web.app.test_client()
        self.web.app.config["TESTING"] = True
        self.login()
        from app.modules.development.factory import get_development_service
        from app.modules.development.document_factory import get_development_document_service

        self.service = get_development_service()
        self.files = get_development_document_service()
        self.project_id = self.service.create_project(
            {"core_number": "8461", "title": "合成页面验收", "owner_username": "007"}, actor="007",
        )["project"]["id"]
        detail = self.service.add_branch(self.project_id, {
            "code": "K8461LA", "side": "left", "owner_username": "007", "drawing_required": 0,
            "drawing_reason": "合成免图成品",
        }, actor="007", expected_version=self.version())
        self.branch_id = detail["branches"][0]["id"]
        detail = self.service.add_component(self.project_id, self.branch_id, {
            "code": "QD8461", "borrowed_code": "QD8070", "name": "球销",
            "task": {"drawing_required": 0, "drawing_reason": "合成免图任务"},
        }, actor="007", expected_version=self.version())
        self.component_id = detail["components"][0]["id"]
        self.task_id = detail["tasks"][0]["id"]

    def version(self):
        return self.service.detail(self.project_id)["project"]["version"]

    def detail_url(self, view="components"):
        return f"/development/{self.project_id}?view={view}&branch={self.branch_id}&component={self.component_id}"

    def post(self, suffix, data):
        return self.client.post(f"/development/{self.project_id}/{suffix}", data={
            "expected_version": str(self.version()), "view": "documents", "branch": self.branch_id,
            "component": self.component_id, **data,
        })

    def upload(self, **changes):
        data = {"kind": "drawing", "number": "QD8461", "title": "合成球销图纸", "label": "A",
                "change_note": "首次登记", "state": "released", "target": f"task|{self.task_id}",
                "file": (io.BytesIO(PDF), "QD8461.pdf"), "form_key": "new-document", **changes}
        response = self.post("documents/upload", data)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[-1500:])
        return self.files.list_documents(self.project_id)[0]

    def test_component_page_keeps_both_codes_and_false_drawing_choice(self):
        response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="code" type="text" value="QD8461"', html)
        self.assertIn('name="borrowed_code" type="text" value="QD8070"', html)
        selected = re.search(r'<select name="drawing_required"[^>]*>(.*?)</select>', html, re.S)
        self.assertIsNotNone(selected)
        self.assertIn('<option value="0" selected>不需图</option>', selected.group(1))
        self.assertIn('data-inspector-field="task.drawing_reason"', html)
        seed = json.loads(html_parser.unescape(re.search(r'data-initial="([^"]+)"', html).group(1)))
        task = next(row for row in seed["tasks"] if row["id"] == self.task_id)
        self.assertEqual(task["drawing_required"], 0)
        self.assertEqual(task["drawing_reason"], "合成免图任务")
        self.assertIn('data-development-form', html)

    def test_new_supplier_is_available_before_first_task_reference(self):
        self.service.save_supplier({"name": "全新合成供应商"}, actor="007")
        html = self.client.get(self.detail_url()).get_data(as_text=True)
        self.assertIn("全新合成供应商", html)

    def test_explicit_borrowing_source_selection_preserves_own_code(self):
        original = next(row for row in self.service.detail(self.project_id)["components"] if row["id"] == self.component_id)
        source_id = original["borrowed_source_id"]
        response = self.post("components/add", {
            "view": "components", "name": "显式选择借用来源", "code": "LOCAL8461",
            "borrowed_source_id": source_id, "borrowed_code": "", "quantity": "1",
        })
        self.assertEqual(response.status_code, 302)
        component = next(row for row in self.service.detail(self.project_id)["components"] if row["code"] == "LOCAL8461")
        self.assertEqual(component["borrowed_source_id"], source_id)
        self.assertEqual(component["borrowed_code"], "QD8070")
        html = self.client.get(self.detail_url()).get_data(as_text=True)
        self.assertIn(f'value="{source_id}" data-source-code="QD8070" selected', html)

    def test_validation_marks_field_and_retains_stale_version(self):
        old_version = self.version()
        self.service.update_project(self.project_id, {"title": "另一页面的更新"}, expected_version=old_version, actor="007")
        response = self.post("update", {"core_number": "8461", "title": "待核对的旧输入",
                                       "expected_version": old_version, "view": "overview", "form_key": "project"})
        self.assertEqual(response.status_code, 409)
        html = response.get_data(as_text=True)
        project_form = re.search(r'<form[^>]+action="[^"]+/update"[^>]*>(.*?)</form>', html, re.S).group(1)
        self.assertIn(f'name="expected_version" value="{old_version}"', project_form)
        self.assertIn('value="待核对的旧输入"', project_form)
        response = self.client.post("/development/create", data={"core_number": "84", "title": "校验错误"})
        self.assertIn('aria-invalid="true"', response.get_data(as_text=True))

    def test_document_history_upload_and_manual_adoption_keep_old_bytes(self):
        first = self.upload()
        first_id = first["revisions"][0]["id"]
        second = self.upload(document_id=first["id"], label="B", change_note="第二版合成图纸",
                             file=(io.BytesIO(PDF + b"\n"), "QD8461-B.pdf"))
        second_id = next(row["id"] for row in second["revisions"] if row["label"] == "B")
        self.assertEqual(second["links"][0]["revision_id"], first_id)
        html = self.client.get(self.detail_url("documents")).get_data(as_text=True)
        self.assertIn("有新修订，尚未采用", html)
        self.assertIn("第二版合成图纸", html)
        response = self.post(f"documents/{second_id}/adopt", {"target": f"task|{self.task_id}"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.files.list_documents(self.project_id)[0]["links"][0]["revision_id"], second_id)
        with self.client.get(f"/development/{self.project_id}/documents/{first_id}/download") as downloaded:
            self.assertEqual(downloaded.data, PDF)
            self.assertEqual(downloaded.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("attachment", downloaded.headers["Content-Disposition"])
        with self.client.get(f"/development/{self.project_id}/documents/{first_id}/preview") as preview:
            self.assertEqual(preview.status_code, 200)
            self.assertIn("inline", preview.headers["Content-Disposition"])

    def test_rejected_upload_retains_inputs_and_does_not_create_revision(self):
        response = self.post("documents/upload", {
            "kind": "drawing", "number": "QD8461", "title": "保留被拒绝的上传信息", "label": "A",
            "change_note": "合成脚本不允许上传", "state": "draft", "form_key": "new-document",
            "file": (io.BytesIO(b"<script>unsafe()</script>"), "unsafe.html"),
        })
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("保留被拒绝的上传信息", html)
        self.assertIn("文件提交失败后需重新选择", html)
        self.assertEqual(self.files.list_documents(self.project_id), [])

    def test_non_pdf_process_attachment_never_renders_inline(self):
        document = self.upload(kind="process", file=(io.BytesIO(b"synthetic process instructions"), "process.txt"))
        revision_id = document["revisions"][0]["id"]
        response = self.client.get(f"/development/{self.project_id}/documents/{revision_id}/preview")
        self.assertEqual(response.status_code, 415)

    def test_state_transition_keeps_adopted_revision_until_confirmation(self):
        document = self.upload(state="draft")
        revision_id = document["revisions"][0]["id"]
        response = self.post(f"documents/{revision_id}/state", {"state": "released", "change_note": "合成发布"})
        self.assertEqual(response.status_code, 302)
        latest = self.files.list_documents(self.project_id)[0]
        self.assertEqual(len(latest["revisions"]), 2)
        self.assertEqual(latest["links"][0]["revision_id"], revision_id)
        self.assertTrue(latest["links"][0]["has_newer_revision"])

    def test_copy_drawings_and_legacy_snapshot_require_source_permissions(self):
        from app.modules.admin.factory import get_admin_service

        user_id = get_admin_service().save_user({
            "username": f"synthetic-editor-{uuid4().hex[:8]}", "password": "test-password",
            "role": "viewer", "active": "1", "permission_overrides": {
                "view_development": "allow", "edit_development": "allow", "view_development_files": "deny",
                "view_product_drawings": "deny", "view_material_drawings": "deny",
            },
        }, actor="007")
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
        with patch("app.modules.development.web_structure.get_development_service") as factory:
            response = self.post(f"branches/{self.branch_id}/copy", {"copy_drawings": "1"})
            self.assertEqual(response.status_code, 403)
            factory.assert_not_called()
        response = self.post("documents/snapshot", {"source_kind": "product", "relative_name": "QD8070.pdf"})
        # Permission decorators redirect HTML requests without invoking the document service.
        self.assertIn(response.status_code, (302, 403))
        second_id = get_admin_service().save_user({
            "username": f"synthetic-files-{uuid4().hex[:8]}", "password": "test-password",
            "role": "viewer", "active": "1", "permission_overrides": {
                "view_development": "allow", "edit_development": "allow", "view_development_files": "allow",
                "view_product_drawings": "deny", "view_material_drawings": "deny",
            },
        }, actor="007")
        with self.client.session_transaction() as session:
            session["user_id"] = second_id
        with patch("app.modules.development.web_documents.get_development_document_service") as factory:
            for source in ("product", "material"):
                response = self.post("documents/snapshot", {"source_kind": source, "relative_name": "QD8070.pdf"})
                self.assertEqual(response.status_code, 403)
            factory.assert_not_called()

    def test_long_untrusted_names_are_escaped(self):
        self.service.update_project(self.project_id, {"title": '<img src=x onerror="unsafe()">'},
                                    expected_version=self.version(), actor="007")
        html = self.client.get(self.detail_url("overview")).get_data(as_text=True)
        self.assertIn("&lt;img", html)
        self.assertNotIn('<img src=x onerror="unsafe()">', html)

    def test_creation_infrastructure_error_is_private_and_retains_inputs(self):
        with patch("app.modules.development.web.get_development_service") as factory:
            factory.return_value.create_project.side_effect = RuntimeError("private/path/and-secret")
            with self.assertLogs("app.modules.development.web", level="ERROR"):
                response = self.client.post("/development/create", data={"core_number": "8461", "title": "仍保留创建资料"})
        self.assertEqual(response.status_code, 500)
        html = response.get_data(as_text=True)
        self.assertIn("仍保留创建资料", html)
        self.assertNotIn("private/path/and-secret", html)
