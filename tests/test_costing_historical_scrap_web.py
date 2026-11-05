from __future__ import annotations

import io
import re
from unittest.mock import Mock, patch
from uuid import uuid4

from tests.web_app_test_base import WebAppTestBase


class HistoricalScrapWebTest(WebAppTestBase):
    path = "/costs/transfer/historical-scrap"
    json_headers = {"Accept": "application/json"}

    def setUp(self):
        self.web.app.config["TESTING"] = True
        self.client = self.web.app.test_client()
        self.login()
        self.maintenance = Mock()
        self.preview = {
            "digest": "signed-preview-token", "material_count": 1, "cost_count": 1,
            "materials": [{"model": "8057", "code": "QA-PLATE", "part": "主板", "net_weight": "0.95", "source_row": 21}],
            "costs": [{"code": "K8057LB", "old_theory": None, "new_theory": "2.48",
                       "old_credit": "0", "new_credit": "1.48"}],
            "reviews": [{"scope": "材料", "code": "QA-CHECK", "part": "副板", "reason": "黄底估算净重", "codes": ["QA-ALT"], "source_row": 22}],
        }
        self.maintenance.preview.return_value = self.preview
        self.maintenance.apply.return_value = {"material_count": 1, "cost_count": 1}
        self.addCleanup(self.web.app.config.update, TESTING=True)
        injected = patch.dict(self.web.app.extensions, historical_scrap_service=self.maintenance)
        injected.start()
        self.addCleanup(injected.stop)

    def upload(self, headers=None):
        return self.client.post(self.path+"/preview", headers=headers or {},
                                data={"file": (io.BytesIO(b"source workbook"), "original.xlsx")})

    def test_same_session_sees_upload_preview_and_confirmed_changes(self):
        for source in ("/costs/import", "/costs/transfer"):
            self.assertIn(f'href="{self.path}"', self.client.get(source).get_data(as_text=True))
        page = self.client.get(self.path)
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn(f'action="{self.path}/preview"', body)
        self.assertNotIn("确认应用预览中的修改", body)
        self.maintenance.preview.assert_not_called()
        self.web.app.config["TESTING"] = False
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)
        headers = {"X-CSRF-Token": csrf}
        self.assertEqual(self.upload(self.json_headers).status_code, 400)
        self.assertEqual(self.client.post(self.path, json={"digest": "old"}, headers=self.json_headers).status_code, 400)
        preview_response = self.upload(headers)
        self.assertEqual(preview_response.status_code, 200)
        body = preview_response.get_data(as_text=True)
        for content in ("K8057LB", "QA-PLATE", "0.95", "1.48", "QA-CHECK", "QA-ALT", "片件重量!E21", "片件重量!E22", "黄底估算净重", 'name="digest" value="signed-preview-token"'):
            self.assertIn(content, body)
        owner = self.maintenance.preview.call_args.kwargs["owner"]
        self.assertTrue(owner)
        self.maintenance.apply.assert_not_called()
        json_preview = self.upload({**headers, **self.json_headers})
        self.assertEqual(json_preview.get_json(), {"ok": True, "preview": self.preview})
        applied = self.client.post(self.path, data={"digest": self.preview["digest"]}, headers=headers)
        self.assertEqual(applied.status_code, 303)
        self.assertEqual(applied.location, self.path)
        self.assertEqual(self.maintenance.apply.call_args.args, (self.preview["digest"],))
        self.assertEqual(self.maintenance.apply.call_args.kwargs["owner"], owner)
        self.assertTrue(self.maintenance.apply.call_args.kwargs["actor"])
        response = self.client.post(self.path, json={"digest": self.preview["digest"]}, headers={**headers, **self.json_headers})
        self.assertEqual(response.get_json(), {"ok": True, "result": {"material_count": 1, "cost_count": 1}})

    def test_zero_eligible_rows_and_stale_preview(self):
        from app.modules.costing.domain import CostingConflict

        self.maintenance.preview.return_value = {**self.preview, "cost_count": 0, "material_count": 0, "materials": [], "costs": []}
        body = self.upload().get_data(as_text=True)
        self.assertIn("QA-CHECK", body)
        self.assertNotIn("确认应用预览中的修改", body)
        self.assertEqual(self.client.post(self.path, json=[]).status_code, 422)
        self.maintenance.apply.assert_not_called()
        self.maintenance.apply.side_effect = CostingConflict("预览后资料发生变化，请重新核对。")
        response = self.client.post(self.path, data={"digest": "expired"})
        self.assertEqual(response.status_code, 409)
        self.assertIn(f'action="{self.path}/preview"', response.get_data(as_text=True))
        response = self.client.post(self.path, json={"digest": "expired"})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["ok"])

    def test_requires_authenticated_admin_and_both_write_permissions(self):
        from app.modules.admin.factory import get_admin_service

        anonymous = self.web.app.test_client()
        self.assertEqual(anonymous.get(self.path, headers=self.json_headers).status_code, 401)
        with self.client.session_transaction() as state:
            admin_id = state["user_id"]
        admin = get_admin_service()
        username = "scrap-rights-" + uuid4().hex[:8]
        admin.save_user({"username": username, "display_name": username, "password": "test-rights-pw",
                         "role": "viewer", "active": "1",
                         "permission_overrides": {"manage_costs": "allow", "manage_material_items": "allow"}},
                        actor="007", actor_user_id=admin_id)
        client = self.web.app.test_client()
        self.assertEqual(client.post("/login", data={"username": username, "password": "test-rights-pw"}).status_code, 302)
        self.assertEqual(client.get(self.path, headers=self.json_headers).status_code, 403)
        self.assertNotIn(f'href="{self.path}"', client.get("/costs/import").get_data(as_text=True))

        # Current administrators inherit all rights. Simulate separately revoked
        # rights to ensure each disclosure and each write route checks all three.
        current = admin.user(admin_id)
        for missing in ("manage_users", "manage_costs", "manage_material_items"):
            limited = {**current, "permissions": set(current["permissions"]) - {missing}}
            with self.subTest(missing=missing), patch.object(type(admin), "user", return_value=limited):
                self.assertEqual(self.client.get(self.path, headers=self.json_headers).status_code, 403)
                self.assertEqual(self.client.post(self.path+"/preview", headers=self.json_headers).status_code, 403)
                self.assertEqual(self.client.post(self.path, json={"digest": "x"}, headers=self.json_headers).status_code, 403)
                for entry in ("/costs/import", "/costs/transfer"):
                    self.assertNotIn(f'href="{self.path}"', self.client.get(entry).get_data(as_text=True))
        self.maintenance.apply.assert_not_called()
        self.maintenance.preview.assert_not_called()
