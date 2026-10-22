"""Tree-grid page protocol and safe synthetic bootstrap integration."""
from __future__ import annotations

import html
import json
import re

from tests.web_app_test_base import WebAppTestBase


class DevelopmentGridPageTest(WebAppTestBase):
    def setUp(self):
        self.client = self.web.app.test_client()
        self.login()
        from app.modules.development.factory import get_development_service

        self.service = get_development_service()
        detail = self.service.create_project({"core_number": "8461", "title": "合成树表", "owner_username": "007"}, actor="007")
        self.project_id = detail["project"]["id"]
        detail = self.service.add_branch(self.project_id, {"code": "K8461LA", "name": "合成左成品", "side": "left"},
                                         expected_version=detail["project"]["version"], actor="007")
        self.branch_id = detail["branches"][0]["id"]
        detail = self.service.save_component_grid(self.project_id, self.branch_id, [{
            "client_id": "pin", "code": "QD8461", "name": '<球销 "合成">', "borrowed_code": "QD8070",
        }], expected_version=detail["project"]["version"], actor="007")["detail"]
        self.component_id = detail["components"][0]["id"]

    def page(self, view="components"):
        response = self.client.get(f"/development/{self.project_id}?view={view}&branch={self.branch_id}&component={self.component_id}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_exact_eight_columns_share_grid_protocol_and_draft_account_scope(self):
        text = self.page()
        table = re.search(r'<table[^>]*development-tree-table.*?</table>', text, re.S).group()
        titles = re.findall(r'<span class="data-grid-static-column-label">(.*?)</span>', table)
        self.assertEqual(titles, ["产品编码", "产品名称", "图纸状态", "借用状态", "借用编码", "开发状态", "责任单位", "目标日期"])
        self.assertIn('data-resizable-grid data-grid-key="development-tree-', text)
        self.assertIn('data-grid-scroll tabindex="0"', text)
        self.assertRegex(text, r'data-user-id="\d+"')
        self.assertIn('data-grid-stale-forms hidden', text)
        self.assertNotIn('form_key" value="new-component"', text)

    def test_bootstrap_escapes_names_and_keeps_own_code_separate_from_borrowing(self):
        text = self.page()
        seed = json.loads(html.unescape(re.search(r'data-initial="([^"]+)"', text).group(1)))
        component = next(item for item in seed["components"] if item["id"] == self.component_id)
        self.assertEqual((component["code"], component["borrowed_code"]), ("QD8461", "QD8070"))
        self.assertEqual(component["name"], '<球销 "合成">')
        self.assertNotIn('<球销 "合成">', text)
        self.assertIn('data-inspector-field="task.actual_date"', text)
        self.assertIn('data-inspector-field="task.owner_username"', text)

    def test_document_entry_prefills_own_component_number_and_name(self):
        text = self.page("documents")
        number = re.search(r'<input[^>]*name="number"[^>]*>', text).group()
        title = re.search(r'<input[^>]*name="title"[^>]*>', text).group()
        self.assertIn('value="QD8461"', number)
        self.assertNotIn('QD8070', number)
        self.assertIn('&lt;球销', title)
