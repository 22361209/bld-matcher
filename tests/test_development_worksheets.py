from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from app.migrations import MIGRATIONS
from app.modules.development.domain import DevelopmentValidationError
from app.modules.development.repository import SQLiteDevelopmentUnitOfWork
from app.modules.development.schema import install_schema
from app.modules.development.service import DevelopmentService
from app.modules.development.worksheet import default_worksheet, project_fields, worksheet_for_display


class DevelopmentWorksheetTest(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.path = Path(root.name) / "test.sqlite3"
        self.legacy_id = str(uuid4())
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE audit_logs (action, target_type, target_key, actor, detail, created_at)")
            install_schema(conn, worksheets=False)
            conn.execute("""INSERT INTO development_projects(id,core_number,title,created_by,created_at,updated_at)
                VALUES (?,'1234','旧单','owner','2026-10-04','2026-10-04')""", (self.legacy_id,))
            migrate = dict(MIGRATIONS)["047_development_worksheets"]
            migrate(conn)
            migrate(conn)
        self.service = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(self.path))
        self.sheet = {**default_worksheet(), "core_code": "EXAMPLE"}

    def test_new_sheet_material_defaults_only_apply_to_the_named_parts(self):
        self.assertEqual([(row["name"], row["material"]) for row in self.sheet["rows"]], [
            ("主体", ""), ("小衬套", "NR"), ("大衬套", "NR"), ("球头", ""),
            ("球壳", "45号钢"), ("球销", "40Cr"), ("球座", ""), ("防尘罩", "CR"),
        ])

    def test_one_core_code_tracks_parts_in_one_version(self):
        first = self.sheet["rows"][0]
        first.update(code="L-ARM", material="锻打", mode="new", developer="工艺组",
                     start_date="2026-11-05", due_date="2026-11-20")
        self.sheet["rows"].insert(1, {**first, "code": "R-ARM", "developer": "另一单位"})
        self.sheet["rows"][2].update(code="B1234", mode="borrowed", borrowed_code="B0123")
        created = self.service.create_worksheet(self.sheet, actor="owner")
        project_id = created["project"]["id"]
        self.assertEqual(created["worksheet"], self.sheet)
        self.assertEqual(created["project"]["core_number"], "EXAMPLE")
        self.assertEqual(created["project"]["status"], "not_started")
        self.sheet["started"] = True
        self.sheet["rows"].pop(3)
        self.sheet["rows"].append({**first, "name": "特制盖板"})
        updated = self.service.update_worksheet(project_id, self.sheet, expected_version=1, actor="owner")
        self.assertEqual(updated["project"]["version"], 2)
        results = self.service.list_projects(query="example", status="in_progress", worksheet=True)
        self.assertEqual([item["worksheet"] for item in results], [self.sheet])
        self.assertEqual([item["id"] for item in self.service.list_projects(worksheet=False)], [self.legacy_id])
        with self.assertRaisesRegex(DevelopmentValidationError, "其他人"):
            self.service.update_worksheet(project_id, created["worksheet"], expected_version=1, actor="other")
        self.assertEqual(self.service.detail(project_id), updated)

    def test_legacy_and_new_writes_cannot_cross_and_invalid_batch_rolls_back(self):
        self.sheet["core_code"] = "SINGLE"
        created = self.service.create_worksheet(self.sheet, actor="owner")
        project_id = created["project"]["id"]
        with self.assertRaises(DevelopmentValidationError):
            self.service.add_branch(project_id, {"code": "X"}, expected_version=1, actor="owner")
        with self.assertRaises(DevelopmentValidationError):
            self.service.update_worksheet(self.legacy_id, self.sheet, expected_version=1, actor="owner")
        for invalid in ({"mode": "borrowed", "borrowed_code": ""}, {"material": "钢" * 1000}, {"side": "left"},
                        {"mode": "new", "start_date": "2026-11-05", "due_date": "2026-11-04"},
                        {"mode": "new", "start_date": "2026-02-31"}, {"mode": "pending", "developer": "工艺组"}):
            with self.subTest(invalid=invalid), self.assertRaises(DevelopmentValidationError):
                payload = deepcopy(self.sheet)
                payload["rows"][0].update(name="不会部分写入")
                payload["rows"][-1].update(invalid)
                self.service.update_worksheet(project_id, payload, expected_version=1, actor="owner")
        self.assertEqual(self.service.detail(project_id), created)
        legacy = self.service.detail(self.legacy_id)
        self.assertNotIn("worksheet", legacy)
        self.assertEqual((legacy["project"]["title"], legacy["project"]["version"]), ("旧单", 1))
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT action,actor FROM audit_logs").fetchall(), [("新增新品开发表", "owner")])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_old_paired_sheet_is_read_without_rewriting_and_only_upgraded_on_edit(self):
        row = {**self.sheet["rows"][0], "name": "臂体", "material": ""}
        paired = {"left_code": "K8461LA", "right_code": "K8461RA", "started": False, "rows": [
            {**row, "side": "left", "code": "L-ARM"},
            {**row, "side": "right", "code": "R-ARM"},
            {**row, "side": "left", "name": "衬套", "material": "锻打"},
            {**row, "side": "right", "name": "衬套"},
        ]}
        fields = project_fields(paired)
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE development_projects SET title=?,core_number=? WHERE id=?",
                               (fields["title"], fields["core_number"], self.legacy_id))
            connection.execute("INSERT INTO development_worksheets(project_id,content) VALUES (?,?)",
                               (self.legacy_id, json.dumps(paired)))
        display = self.service.detail(self.legacy_id)["worksheet"]
        self.assertEqual(display["core_code"], "8461")
        self.assertEqual([(line["name"], line["code"]) for line in display["rows"]],
                         [("臂体", "L-ARM"), ("臂体", "R-ARM"), ("衬套 (L)", ""), ("衬套 (R)", "")])
        self.assertEqual([line["material"] for line in display["rows"]], ["", "", "锻打", ""])
        self.assertTrue(all("side" not in line for line in display["rows"]))
        with SQLiteDevelopmentUnitOfWork(self.path) as unit:
            self.assertEqual(unit.repository.export_aggregate(self.legacy_id)["worksheet"], paired)
        self.service.update_worksheet(self.legacy_id, {**display, "started": True}, expected_version=1, actor="owner")
        with SQLiteDevelopmentUnitOfWork(self.path) as unit:
            upgraded = unit.repository.export_aggregate(self.legacy_id)
        self.assertEqual(upgraded["worksheet"], {**display, "started": True})
        self.assertEqual(upgraded["project"]["title"], "8461")
        for left, right, expected in [
            ("", "SINGLE-R", "SINGLE-R"), ("X", "Y", "X / Y"), ("K8461LA", "K8111RA", "K8461LA / K8111RA"),
            ("K1555-2111L", "", "K1555-2111L"), ("", "K8461RA", "8461"),
        ]:
            with self.subTest(left=left, right=right):
                legacy = {**paired, "left_code": left, "right_code": right}
                self.assertEqual(worksheet_for_display(legacy)["core_code"], expected)
