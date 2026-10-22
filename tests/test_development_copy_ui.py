"""Copy review UI contracts, isolated Web mutations and native-module selection behavior."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from tests.web_app_test_base import WebAppTestBase


class DevelopmentCopyUiTest(WebAppTestBase):
    def setUp(self):
        from app.modules.development.factory import get_development_service

        self.client = self.web.app.test_client()
        self.web.app.config["TESTING"] = True
        self.login()
        self.service = get_development_service()
        self.project_id = self.service.create_project({"core_number": "8461", "title": "复制核对合成测试", "owner_username": "007"}, actor="007")["project"]["id"]
        detail = self.service.add_branch(self.project_id, {"code": "K8461LA", "side": "left", "owner_username": "007", "drawing_required": 0, "drawing_reason": "合成免图成品"}, expected_version=self.version(), actor="007")
        self.branch_id = detail["branches"][0]["id"]
        self.components = []
        for index, (name, code, borrowed) in enumerate((("球头总成", "BJ8461", ""), ("球销", "QD8461", "QD8070"), ("防尘罩", "FC8461", ""))):
            detail = self.service.add_component(self.project_id, self.branch_id, {
                "name": name, "code": code, "borrowed_code": borrowed,
                "parent_id": self.components[0]["id"] if index else None,
                "task": {"owner_username": "007", "status": "completed", "actual_date": "2026-10-02", "drawing_required": 0, "drawing_reason": "合成免图零件"},
            }, expected_version=self.version(), actor="007")
            self.components.append(next(row for row in detail["components"] if row["name"] == name))

    def version(self):
        return self.service.detail(self.project_id)["project"]["version"]

    def payload(self, **overrides):
        return {"form_key": "copy-branch", "view": "components", "branch": self.branch_id,
                "expected_version": self.version(), "code": "用户手填完整成品码", "name": "右成品", "side": "right",
                "owner_username": "007", "drawing_required": "0", "drawing_reason": "新成品免图原因", "drawing_status": "not_started",
                **{f"mode_{row['id']}": "independent" for row in self.components}, **overrides}

    def post_copy(self, data):
        return self.client.post(f"/development/{self.project_id}/branches/{self.branch_id}/copy", data=data)

    def page(self):
        return self.client.get(f"/development/{self.project_id}?view=components&branch={self.branch_id}&copy=1")

    def test_review_table_has_hierarchy_separate_codes_and_independent_defaults(self):
        response = self.page()
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        copy_form = re.search(r'<form[^>]*data-development-copy[^>]*>(.*?)</form>', html, re.S).group(1)
        self.assertIn('name="csrf_token"', copy_form)
        self.assertIn('data-copy-depth="0"', copy_form)
        self.assertEqual(copy_form.count('data-copy-depth="1"'), 2)
        self.assertLess(copy_form.index('data-copy-depth="0"'), copy_form.index('data-copy-depth="1"'))
        self.assertIn('选中项共用', copy_form)
        self.assertIn('选中项独立', copy_form)
        self.assertNotIn('value="shared" selected', copy_form)
        self.assertEqual(copy_form.count('value="independent" selected'), 3)
        pin = self.components[1]
        self.assertIn(f'name="code_{pin["id"]}" type="text" maxlength="200" value="QD8461"', copy_form)
        self.assertIn(f'name="borrowed_code_{pin["id"]}" type="text" maxlength="200" value="QD8070"', copy_form)
        self.assertNotRegex(copy_form, r'name="copy_drawings"[^>]*checked')
        self.assertIn('另行确认', copy_form)
        self.assertIn('development_copy.css', html)
        self.assertIn('data-copy-refresh hidden', copy_form)

    def test_web_copy_saves_overrides_and_hierarchy_in_one_version_then_focuses_new_branch(self):
        pin = self.components[1]
        before_version = self.version()
        data = self.payload(**{f'mode_{pin["id"]}': "shared", f'code_{pin["id"]}': "QD8461-手录目标", f'borrowed_code_{pin["id"]}': "QD9088"})
        response = self.post_copy(data)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[-2000:])
        state = self.service.detail(self.project_id)
        branch = next(row for row in state["branches"] if row["id"] != self.branch_id)
        target = {row["name"]: row for row in state["components"] if row["branch_id"] == branch["id"]}
        original = {row["name"]: row for row in state["components"] if row["branch_id"] == self.branch_id}
        self.assertEqual(state["project"]["version"], before_version + 1)
        self.assertEqual(branch["code"], "用户手填完整成品码")
        self.assertEqual(target["球销"]["code"], "QD8461-手录目标")
        self.assertEqual(target["球销"]["borrowed_code"], "QD9088")
        self.assertNotEqual(target["球销"]["borrowed_source_id"], original["球销"]["borrowed_source_id"])
        self.assertEqual(target["球销"]["task_id"], original["球销"]["task_id"])
        self.assertEqual(target["球销"]["parent_id"], target["球头总成"]["id"])
        self.assertEqual(target["防尘罩"]["parent_id"], target["球头总成"]["id"])
        self.assertEqual(original["球销"]["code"], "QD8461")
        self.assertEqual(original["球销"]["borrowed_code"], "QD8070")
        location = parse_qs(urlsplit(response.location).query)
        self.assertEqual(location["view"], ["components"])
        self.assertEqual(location["branch"], [branch["id"]])
        self.assertNotIn("component", location)

    def test_clearing_target_borrowing_clears_uuid_and_preserves_source(self):
        pin = self.components[1]
        response = self.post_copy(self.payload(**{f'borrowed_code_{pin["id"]}': ""}))
        self.assertEqual(response.status_code, 302)
        state = self.service.detail(self.project_id)
        copied = next(row for row in state["components"] if row["branch_id"] != self.branch_id and row["name"] == "球销")
        source = next(row for row in state["components"] if row["id"] == pin["id"])
        self.assertEqual(copied["borrowed_code"], "")
        self.assertIsNone(copied["borrowed_source_id"])
        self.assertEqual(source["borrowed_code"], "QD8070")
        self.assertEqual(source["borrowed_source_id"], pin["borrowed_source_id"])

    def test_unchanged_target_borrowing_preserves_original_source_uuid(self):
        pin = self.components[1]
        response = self.post_copy(self.payload(**{f'code_{pin["id"]}': "QD8461R-手填", f'borrowed_code_{pin["id"]}': "QD8070"}))
        self.assertEqual(response.status_code, 302)
        state = self.service.detail(self.project_id)
        copied = next(row for row in state["components"] if row["branch_id"] != self.branch_id and row["name"] == "球销")
        self.assertEqual(copied["code"], "QD8461R-手填")
        self.assertEqual(copied["borrowed_code"], "QD8070")
        self.assertEqual(copied["borrowed_source_id"], pin["borrowed_source_id"])
        self.assertEqual(len(state["sources"]), 1)

    def test_independent_copy_resets_completed_progress_and_dates(self):
        response = self.post_copy(self.payload())
        self.assertEqual(response.status_code, 302)
        state = self.service.detail(self.project_id)
        target = [row for row in state["components"] if row["branch_id"] != self.branch_id]
        task_by_id = {row["id"]: row for row in state["tasks"]}
        for row in target:
            task = task_by_id[row["task_id"]]
            self.assertEqual(task["status"], "not_started")
            self.assertEqual(task["actual_date"], "")
        branch = next(row for row in state["branches"] if row["id"] != self.branch_id)
        self.assertEqual(branch["production_ready"], 0)
        self.assertEqual(branch["actual_date"], "")
        self.assertEqual(branch["confirmed_by"], "")

    def test_invalid_override_rolls_back_every_row_and_keeps_review_inputs(self):
        before = self.service.detail(self.project_id)
        pin = self.components[1]
        response = self.post_copy(self.payload(**{f'code_{pin["id"]}': "X" * 201, f'mode_{pin["id"]}': "shared", "copy_selected": pin["id"]}))
        self.assertEqual(response.status_code, 400)
        after = self.service.detail(self.project_id)
        for field in ("branches", "components", "tasks", "sources"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after["project"]["version"], before["project"]["version"])
        html = response.get_data(as_text=True)
        self.assertIn('value="' + "X" * 201 + '"', html)
        self.assertIn('value="shared" selected', html)
        self.assertIn(f'name="copy_selected" value="{pin["id"]}" data-copy-selected aria-label="选择 球销" checked', html)

    def test_stale_version_rejects_copy_and_preserves_entered_target_code(self):
        data = self.payload(**{f'code_{self.components[1]["id"]}': "未丢失的目标编码"})
        self.service.update_project(self.project_id, {"title": "并发修改"}, expected_version=self.version(), actor="007")
        version = self.version()
        response = self.post_copy(data)
        self.assertEqual(response.status_code, 409)
        self.assertIn('value="未丢失的目标编码"', response.get_data(as_text=True))
        self.assertEqual(len(self.service.detail(self.project_id)["branches"]), 1)
        self.assertEqual(self.version(), version)

    def test_foreign_override_identifier_is_rejected_atomically(self):
        version = self.version()
        response = self.post_copy(self.payload(**{f"code_{uuid4()}": "不可写入其他行"}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.service.detail(self.project_id)["branches"]), 1)
        self.assertEqual(self.version(), version)

    def test_service_rejects_override_fields_outside_the_two_code_columns(self):
        with self.assertRaises(ValueError):
            self.service.copy_branch(self.project_id, self.branch_id,
                                     {"code": "K8461RA", "component_codes": {self.components[0]["id"]: {"task_id": self.components[1]["task_id"]}}},
                                     modes={row["id"]: "independent" for row in self.components}, expected_version=self.version(), actor="007")
        self.assertEqual(len(self.service.detail(self.project_id)["branches"]), 1)

    def test_bulk_mode_module_only_changes_selected_rows_and_blocks_stale_review(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node unavailable: copy-selection browser logic not run")
        module = (Path(__file__).resolve().parents[1] / "static/pages/development_copy.js").as_uri()
        script = r'''
import assert from 'node:assert/strict';
const {initDevelopmentCopy, setSelectedCopyMode, updateCopySelection} = await import(MODULE);
const rows = [true, false, true].map(checked => {
  const checkbox = {checked}; const mode = {value:'independent', changes:0, dispatchEvent(){this.changes += 1;}};
  return {checkbox, mode, querySelector(key){return key === '[data-copy-selected]' ? checkbox : mode;}};
});
const selectAll = {checked:false, indeterminate:false};
const buttons = [{disabled:true},{disabled:true}];
const status = {textContent:''}; const selectionStatus = {textContent:''}; const refresh = {hidden:true};
const handlers = {}; const disclosure = {dataset:{copyReady:'true'}, querySelector(){return null;}};
const form = {dataset:{}, querySelectorAll(key){return key === '[data-copy-row]' ? rows : buttons;},
  querySelector(key){return {'[data-copy-select-all]':selectAll,'[data-copy-status]':status,'[data-copy-selection-status]':selectionStatus,'[data-copy-refresh]':refresh}[key];},
  addEventListener(key, fn){handlers[key]=fn;}, closest(){return disclosure;}};
assert.equal(updateCopySelection(form).length,2); assert.equal(selectAll.indeterminate,true);
assert.equal(setSelectedCopyMode(form,'shared'),2); assert.deepEqual(rows.map(row=>row.mode.value),['shared','independent','shared']);
assert.equal(setSelectedCopyMode(form,'unexpected'),0); assert.deepEqual(rows.map(row=>row.mode.value),['shared','independent','shared']);
rows.forEach(row=>row.checkbox.checked=false); assert.equal(setSelectedCopyMode(form,'independent'),0); assert.ok(buttons.every(button=>button.disabled));
globalThis.window = new EventTarget();
const root = {body:{dataset:{page:'development.detail'}},querySelectorAll(key){return key === '[data-development-copy]' ? [form] : [disclosure];}};
initDevelopmentCopy(root);
selectAll.checked=true; handlers.change({target:{checked:true,matches(key){return key.includes('[data-copy-select-all]');}}});
assert.ok(rows.every(row=>row.checkbox.checked)); assert.equal(selectAll.checked,true); assert.equal(selectAll.indeterminate,false);
assert.equal(setSelectedCopyMode(form,'independent'),3); assert.ok(rows.every(row=>row.mode.value==='independent'));
window.dispatchEvent(new Event('development:grid-saved'));
let prevented=false; handlers.submit({preventDefault(){prevented=true;}});
assert.equal(prevented,true); assert.equal(refresh.hidden,false); assert.equal(form.dataset.copyStale,'true');
console.log('copy module: selection, invalid mode, empty selection, select all, reset, stale submit passed');
'''.replace("MODULE", repr(module))
        completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
