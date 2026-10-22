"""Cross-layer acceptance for the eight-column development tree, using synthetic data."""
from __future__ import annotations

import io
import json
import sqlite3
from html.parser import HTMLParser
from uuid import UUID

from tests.web_app_test_base import WebAppTestBase


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
REFERENCE_COLUMNS = ("产品编码", "产品名称", "图纸状态", "借用状态", "借用编码", "开发状态", "责任单位", "目标日期")


class TreePageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.initial = None
        self.headers = []
        self.in_tree_table = False
        self.in_header = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if "data-development-grid" in attrs:
            self.initial = json.loads(attrs["data-initial"])
        if tag == "table" and "development-tree-table" in attrs.get("class", "").split():
            self.in_tree_table = True
        if self.in_tree_table and tag == "th":
            self.in_header = True
            self.headers.append("")

    def handle_data(self, data):
        if self.in_header:
            self.headers[-1] += data

    def handle_endtag(self, tag):
        if tag == "th":
            self.in_header = False
        if tag == "table":
            self.in_tree_table = False


class TestDevelopmentTreeAcceptance(WebAppTestBase):
    def setUp(self):
        from app.modules.development.factory import get_development_service
        from app.modules.development.document_factory import get_development_document_service

        self.client = self.web.app.test_client()
        self.web.app.config["TESTING"] = True
        self.login()
        self.service = get_development_service()
        self.files = get_development_document_service()
        self.project_id = self.service.create_project({
            "core_number": "8461", "title": "合成八列表格验收", "owner_username": "007",
            "planned_date": "2026-11-30", "notes": "既有开发单备注不能丢失",
        }, actor="007")["project"]["id"]
        detail = self.service.add_branch(self.project_id, {
            "code": "K8461LA", "name": "合成左控制臂", "side": "left", "owner_username": "007",
            "planned_date": "2026-11-30", "drawing_required": 0, "drawing_reason": "合成免图成品",
        }, expected_version=self.version(), actor="007")
        self.branch_id = detail["branches"][0]["id"]

    def detail(self):
        return self.service.detail(self.project_id)

    def version(self):
        return self.detail()["project"]["version"]

    def batch(self, rows):
        return self.service.save_component_grid(
            self.project_id, self.branch_id, rows, expected_version=self.version(), actor="007",
        )

    def reference_rows(self):
        rows = []
        for key, code, name, borrowed, parent in (
            ("weld", "K8461LD", "焊接组件", "", None),
            ("large", "B8461", "大衬套", "", None),
            ("small", "C8461", "小衬套", "C8070", None),
            ("ball", "Q8461", "悬挂球头", "", None),
            ("shell", "QK8461", "球壳", "", "ball"),
            ("pin", "QD8461", "球销", "QD8070", "ball"),
            ("boot", "FC8461", "防尘罩", "", "ball"),
        ):
            row = {"client_id": key, "code": code, "name": name, "borrowed_code": borrowed, "sort_order": len(rows),
                   "task": {"development_mode": "borrowed" if borrowed else "new", "owner_username": "007",
                            "planned_date": "2026-11-30", "drawing_status": "ready", "status": "in_progress"}}
            if parent:
                row["parent_client_id"] = parent
            rows.append(row)
        return rows

    def upload_pin(self, task_id, *, document_id=None, label="A"):
        from werkzeug.datastructures import FileStorage

        data = {"kind": "drawing", "number": "QD8461", "title": "合成球销图纸", "label": label,
                "change_note": f"合成修订 {label}", "state": "released", "target_type": "task", "target_id": task_id}
        if document_id:
            data["document_id"] = document_id
        return self.files.upload(
            self.project_id, data,
            FileStorage(stream=io.BytesIO(PDF + label.encode()), filename=f"QD8461-{label}.pdf", content_type="application/pdf"),
            expected_version=self.version(), actor="007",
        )

    def schema_snapshot(self):
        # This is a disposable fixture, never the runtime database.
        with sqlite3.connect(self.web.DB_PATH) as connection:
            schema = connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            migrations = connection.execute("SELECT id FROM schema_migrations ORDER BY id").fetchall()
            return schema, migrations

    def test_seven_reference_rows_save_as_one_version_without_new_schema(self):
        before_schema = self.schema_snapshot()
        version = self.version()
        result = self.batch(self.reference_rows())
        self.assertEqual(self.schema_snapshot(), before_schema)
        self.assertEqual(result["detail"]["project"]["version"], version + 1)
        mapping = result["client_ids"]
        self.assertEqual(set(mapping), {"weld", "large", "small", "ball", "shell", "pin", "boot"})
        for identifier in mapping.values():
            UUID(identifier)
        components = {row["id"]: row for row in self.detail()["components"]}
        self.assertEqual(len(components), 7)
        self.assertEqual(len(self.detail()["tasks"]), 7)
        for key in ("shell", "pin", "boot"):
            self.assertEqual(components[mapping[key]]["parent_id"], mapping["ball"])
        for key in ("weld", "large", "small", "ball"):
            self.assertFalse(components[mapping[key]]["parent_id"])
        for key, code, borrowed in (("small", "C8461", "C8070"), ("pin", "QD8461", "QD8070")):
            row = components[mapping[key]]
            self.assertEqual((row["code"], row["borrowed_code"]), (code, borrowed))
            UUID(row["borrowed_source_id"])

    def test_partial_grid_edit_keeps_identity_and_hidden_details(self):
        result = self.batch(self.reference_rows())
        pin_id = result["client_ids"]["pin"]
        pin = next(row for row in self.detail()["components"] if row["id"] == pin_id)
        self.service.update_component(self.project_id, pin_id, {
            "quantity": 2, "position": "合成压装位置", "notes": "保留组件低频备注",
        }, expected_version=self.version(), actor="007")
        self.service.update_task(self.project_id, pin["task_id"], {
            "material_grade": "40Cr", "forming_process": "合成锻造", "notes": "保留任务低频备注",
        }, expected_version=self.version(), actor="007")
        result = self.batch([{"id": pin_id, "name": "球销修订名称", "task": {"planned_date": "2026-12-01"}}])
        updated = next(row for row in result["detail"]["components"] if row["id"] == pin_id)
        self.assertEqual(updated["task_id"], pin["task_id"])
        self.assertEqual(updated["borrowed_source_id"], pin["borrowed_source_id"])
        self.assertEqual((updated["code"], updated["borrowed_code"]), ("QD8461", "QD8070"))
        self.assertEqual((updated["quantity"], updated["position"], updated["notes"]),
                         (2, "合成压装位置", "保留组件低频备注"))
        task = next(row for row in result["detail"]["tasks"] if row["id"] == pin["task_id"])
        self.assertEqual((task["material_grade"], task["forming_process"], task["notes"]),
                         ("40Cr", "合成锻造", "保留任务低频备注"))
        self.assertEqual(task["planned_date"], "2026-12-01")
        self.assertEqual(len(result["detail"]["components"]), 7)

    def test_shared_task_grid_edit_preserves_fixed_revision_and_each_own_code(self):
        result = self.batch(self.reference_rows())
        pin_id = result["client_ids"]["pin"]
        pin = next(row for row in self.detail()["components"] if row["id"] == pin_id)
        self.upload_pin(pin["task_id"])
        document = self.files.list_documents(self.project_id)[0]
        revision_a = document["links"][0]["revision_id"]
        self.upload_pin(pin["task_id"], document_id=document["id"], label="B")
        left_components = [row for row in self.detail()["components"] if row["branch_id"] == self.branch_id]
        self.service.copy_branch(self.project_id, self.branch_id, {
            "code": "K8461RA", "side": "right", "owner_username": "007",
            "drawing_required": 0, "drawing_reason": "合成免图成品",
        }, modes={row["id"]: "shared" for row in left_components}, expected_version=self.version(), actor="007")
        right_pin = next(row for row in self.detail()["components"]
                         if row["branch_id"] != self.branch_id and row["task_id"] == pin["task_id"])
        self.batch([{"id": pin_id, "code": "QD8461-L录入值", "task": {"status": "not_started"}}])
        detail = self.detail()
        components = {row["id"]: row for row in detail["components"]}
        self.assertEqual(components[pin_id]["code"], "QD8461-L录入值")
        self.assertEqual(components[right_pin["id"]]["code"], "QD8461")
        self.assertEqual(components[right_pin["id"]]["borrowed_code"], "QD8070")
        task = next(row for row in detail["tasks"] if row["id"] == pin["task_id"])
        self.assertEqual(task["status"], "not_started")
        document = self.files.list_documents(self.project_id)[0]
        self.assertEqual(document["links"][0]["revision_id"], revision_a)
        self.assertTrue(document["links"][0]["has_newer_revision"])

    def test_explicit_new_supplier_is_reused_within_batch_and_owner_stays_distinct(self):
        rows = self.reference_rows()[:2]
        for row in rows:
            row["task"].update(supplier_name="合成责任单位", create_supplier=True, procurement_mode="purchased")
        result = self.batch(rows)
        tasks = result["detail"]["tasks"]
        self.assertEqual(len({row["supplier_id"] for row in tasks}), 1)
        self.assertTrue(tasks[0]["supplier_id"])
        self.assertTrue(all(row["owner_username"] == "007" for row in tasks))
        self.assertEqual(len([row for row in self.service.list_suppliers() if row["name"] == "合成责任单位"]), 1)

    def test_authenticated_grid_adapter_returns_canonical_rows_and_uuid_mapping(self):
        version = self.version()
        response = self.client.post(
            f"/development/{self.project_id}/branches/{self.branch_id}/grid/save",
            json={"expected_version": version, "rows": self.reference_rows()},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[-1000:])
        result = response.get_json()
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], version + 1)
        self.assertEqual(len(result["client_ids"]), 7)
        self.assertEqual(len(result["detail"]["components"]), 7)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_component_lookup_exposes_adopted_a_and_manual_upgrade_to_b(self):
        result = self.batch(self.reference_rows())
        pin_id = result["client_ids"]["pin"]
        pin = next(row for row in self.detail()["components"] if row["id"] == pin_id)
        self.upload_pin(pin["task_id"])
        document = self.files.list_documents(self.project_id)[0]
        revision_a = document["links"][0]["revision_id"]
        self.upload_pin(pin["task_id"], document_id=document["id"], label="B")
        response = self.client.get(f"/development/{self.project_id}/lookup/components/{pin_id}")
        self.assertEqual(response.status_code, 200)
        context = response.get_json()
        self.assertTrue(context["ok"])
        self.assertEqual(context["upload_defaults"], {
            "number": "QD8461", "title": "球销", "target": f"task|{pin['task_id']}",
        })
        self.assertEqual(context["component"]["borrowed_code"], "QD8070")
        self.assertEqual(context["documents"][0]["revision_id"], revision_a)
        self.assertTrue(context["documents"][0]["has_newer_revision"])
        self.assertIn("preview_url", context["documents"][0])
        self.assertNotIn("storage_path", context["documents"][0])

    def test_component_page_matches_exact_eight_reference_columns_and_embeds_tree(self):
        self.batch(self.reference_rows())
        response = self.client.get(
            f"/development/{self.project_id}?view=components&branch={self.branch_id}"
        )
        self.assertEqual(response.status_code, 200)
        page = TreePageParser()
        html = response.get_data(as_text=True)
        page.feed(html)
        self.assertEqual(tuple(label.strip() for label in page.headers), REFERENCE_COLUMNS)
        self.assertIsNotNone(page.initial)
        self.assertEqual(len(page.initial["components"]), 7)
        self.assertEqual(page.initial["project"]["version"], self.version())
        self.assertIn('data-grid-action="save"', html)
        self.assertIn('data-grid-action="sibling"', html)
        self.assertIn('data-grid-action="child"', html)
        self.assertNotIn('value="new-component"', html)
