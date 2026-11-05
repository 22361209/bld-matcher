from __future__ import annotations

import io
import json
import re
import time
from unittest.mock import patch
from uuid import uuid4

from tests.web_app_test_base import WebAppTestBase


class CostingTransferWebTest(WebAppTestBase):
    def setUp(self):
        from app.modules.costing.repository import initialize_costing_database
        from app.modules.costing.service import CostingService
        from app.modules.costing.transfer_service import CostingTransferService

        self.web.app.config["TESTING"] = True
        self.client = self.web.app.test_client()
        self.login()
        with self.web.connect(self.web.DB_PATH) as connection:
            connection.execute("DELETE FROM costing_revisions")
            connection.execute("DELETE FROM costing_records")
            connection.execute("DELETE FROM costing_reference_data")
            connection.commit()
        source_path = self.root / uuid4().hex / "source.sqlite3"
        initialize_costing_database(source_path)
        source = CostingService(source_path)
        created = source.create({"code": "TRANSFER-" + uuid4().hex[:8], "kind": "arm",
                                 "items": [{"name": "材料", "amount": "100"}]}, actor="original-user")
        self.record = source.update(created["id"], {"suggested_tax_price_override": "145"},
                                    expected_version=1, actor="original-editor")
        self.snapshot = CostingTransferService(source_path).export_snapshot()
        self.target = self.web.app.extensions["costing_service"]

    def tearDown(self):
        self.web.app.config["TESTING"] = True

    def csrf(self, client=None):
        response = (client or self.client).get("/costs/transfer")
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)

    def preview(self, *, client=None, csrf=None, json_response=True):
        headers = {"X-CSRF-Token": csrf or self.csrf(client)}
        if json_response:
            headers["Accept"] = "application/json"
        return (client or self.client).post("/costs/transfer/preview", headers=headers,
            data={"file": (io.BytesIO(json.dumps(self.snapshot).encode()), "costing-transfer.json")})

    def apply(self, token, *, client=None, csrf=None):
        return (client or self.client).post("/costs/transfer/apply", json={"preview_token": token}, headers={
            "Accept": "application/json", "X-CSRF-Token": csrf or self.csrf(client),
        })

    def test_authenticated_admin_multipart_preserves_ids_versions_history_and_prices(self):
        csrf = self.csrf()
        self.web.app.config["TESTING"] = False
        preview = self.preview(csrf=csrf)
        self.assertEqual(preview.status_code, 200, preview.get_data(as_text=True))
        payload = preview.get_json()
        self.assertEqual((payload["preview"]["records"], payload["preview"]["revisions"]), (1, 2))
        self.assertEqual(self.target.list()["total"], 0)
        result = self.apply(payload["preview_token"], csrf=csrf)
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertEqual(self.target.get(self.record["id"]), self.record)
        history = self.target.revisions(self.record["id"])
        self.assertEqual([row["version"] for row in history], [2, 1])
        self.assertEqual(history[0]["actor"], "original-editor")
        export = self.client.get("/costs/transfer/export")
        self.assertEqual(export.get_json()["digest"], self.snapshot["digest"])
        self.assertIn("no-store", export.headers["Cache-Control"])
        self.assertEqual(self.apply(payload["preview_token"], csrf=csrf).status_code, 409)

    def test_csrf_is_required_for_preview_and_apply(self):
        self.web.app.config["TESTING"] = False
        preview = self.client.post("/costs/transfer/preview", headers={"Accept": "application/json"},
            data={"file": (io.BytesIO(json.dumps(self.snapshot).encode()), "costing-transfer.json")})
        self.assertEqual(preview.status_code, 400)
        token = self.preview().get_json()["preview_token"]
        self.assertEqual(self.client.post("/costs/transfer/apply", json={"preview_token": token},
                                         headers={"Accept": "application/json"}).status_code, 400)
        self.assertEqual(self.target.list()["total"], 0)

    def test_transfer_requires_admin_even_for_cost_maintainers(self):
        from app.modules.admin.factory import get_admin_service

        anonymous = self.web.app.test_client()
        self.assertEqual(anonymous.get("/costs/transfer", headers={"Accept": "application/json"}).status_code, 401)
        with self.client.session_transaction() as state:
            admin_id = state["user_id"]
        username = "cost-maintainer-" + uuid4().hex[:8]
        get_admin_service().save_user({"username": username, "display_name": username,
            "password": "maintainer-password", "role": "viewer", "active": "1",
            "permission_overrides": {"view_costs": "allow", "manage_costs": "allow"}},
            actor="007", actor_user_id=admin_id)
        client = self.web.app.test_client()
        client.post("/login", data={"username": username, "password": "maintainer-password"})
        self.assertEqual(client.get("/costs", headers={"Accept": "application/json"}).status_code, 200)
        for path in ("/costs/transfer", "/costs/transfer/export", "/costs/transfer/line-codes"):
            self.assertEqual(client.get(path, headers={"Accept": "application/json"}).status_code, 403)
        for path in ("/costs/transfer/preview", "/costs/transfer/apply", "/costs/transfer/line-codes"):
            self.assertEqual(client.post(path, json={}, headers={"Accept": "application/json"}).status_code, 403)

    def test_line_codes_preview_csrf_stale_digest_apply_and_repeat(self):
        path = "/costs/transfer/line-codes"
        record = self.target.create({"code": "QA-LINES", "items": [
            {"category": "外箱", "name": "54*41*15.5", "mode": "fixed", "amount": "1"},
            *[{"category": "配件", "name": f"衬套 C{8000 + n}", "amount": "2"} for n in range(31)],
            *[{"category": "核对", "name": f"零件 C{9000 + n}", "product_code": f"OLD-{n}",
               "amount": "3"} for n in range(34)],
        ]}, actor="seed", source="excel")
        self.web.app.config["TESTING"] = False
        self.assertIn(path, self.client.get("/costs/transfer").get_data(as_text=True))
        response = self.client.get(path)
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("待更新 1 个型号，共 66 行", body)
        self.assertIn("纸箱整理 1 行，产品编码补全 31 行", body)
        self.assertIn("原物料属性", body)
        self.assertIn("新物料属性", body)
        self.assertIn("变更示例（30 行）", body)
        self.assertIn("需人工核对（34 行）", body)
        self.assertEqual(body.count("项目料号与已有产品编码不同"), 34)
        self.assertIn("零件 C9033", body)
        self.assertEqual(body.count(f'href="/costs/{record["id"]}">QA-LINES</a>'), 34)
        token = re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)
        digest = re.search(r'name="digest" value="([^"]+)"', body).group(1)
        self.assertEqual(self.client.post(path, data={"digest": digest},
                         headers={"Accept": "application/json"}).status_code, 400)
        self.assertEqual(self.target.get(record["id"])["version"], 1)
        self.assertEqual(self.client.post(path, data={"csrf_token": token}).status_code, 422)
        stale = self.client.post(path, data={"csrf_token": token, "digest": "0" * 64})
        self.assertEqual(stale.status_code, 409)
        self.assertIn("成本资料已变化", stale.get_data(as_text=True))
        submitted = {"csrf_token": token, "digest": digest, "actor": "spoof"}
        accepted = self.client.post(path, data=submitted)
        self.assertEqual((accepted.status_code, accepted.location), (303, path))
        after = self.client.get(accepted.location).get_data(as_text=True)
        self.assertIn("已更新 1 个型号，共 66 行成本明细", after)
        items = self.target.get(record["id"])["items"]
        self.assertTrue(all(item.get("material_attribute") for item in items))
        self.assertEqual(items[-1]["product_code"], "OLD-33")
        for attribute in ("外购", "自制", "委外加工"):
            count = sum(item["material_attribute"] == attribute for item in items)
            self.assertIn(f"{attribute} {count} 行", body)
        self.assertNotIn("应用本次整理", after)
        self.assertEqual(self.target.revisions(record["id"])[0]["actor"], "007")
        repeated = self.client.post(path, data=submitted, follow_redirects=True)
        self.assertIn("已更新 0 个型号，共 0 行成本明细", repeated.get_data(as_text=True))
        self.assertEqual(self.target.get(record["id"])["version"], 2)

        items[-1]["material_attribute"] = ""
        cleared = self.client.post(f"/costs/{record['id']}", json={"items": items, "version": 2},
                                   headers={"X-CSRF-Token": token})
        self.assertEqual(cleared.status_code, 200)
        self.assertNotIn("应用本次整理", self.client.get(path).get_data(as_text=True))
        self.assertEqual(self.client.post(path, data=submitted).status_code, 303)
        untouched = self.target.get(record["id"])
        self.assertEqual((untouched["version"], untouched["items"][-1]["material_attribute"]), (3, ""))

    def test_preview_belongs_to_exact_login_session_and_expires(self):
        token = self.preview().get_json()["preview_token"]
        other = self.web.app.test_client()
        other.post("/login", data={"username": "007", "password": "test-admin-pw"})
        self.assertEqual(self.apply(token, client=other).status_code, 422)
        with patch("itsdangerous.timed.TimestampSigner.get_timestamp", return_value=int(time.time()) + 7200):
            expired = self.apply(token)
        self.assertEqual(expired.status_code, 422)
        self.assertIn("过期", expired.get_json()["error"])
        self.assertEqual(self.target.list()["total"], 0)

    def test_transfer_html_confirmation_and_private_errors(self):
        from app.modules.costing.transfer_service import CostingTransferService

        preview = self.preview(json_response=False)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("本次核对：1 个型号 · 1 项明细 · 2 条修改记录", preview.get_data(as_text=True))
        self.assertNotIn("built-in method", preview.get_data(as_text=True))
        self.assertIn("确认接入成本资料", preview.get_data(as_text=True))
        self.assertIn('action="/costs/transfer/apply"', preview.get_data(as_text=True))
        invalid = self.client.post("/costs/transfer/preview", data={"file": (io.BytesIO(b"bad"), "bad.json")})
        self.assertEqual(invalid.status_code, 422)
        self.assertIn('data-page="costing.transfer"', invalid.get_data(as_text=True))
        with patch.object(CostingTransferService, "preview", side_effect=RuntimeError("/private/secret.sqlite3")):
            result = self.preview()
        self.assertEqual(result.status_code, 500)
        self.assertNotIn("secret.sqlite3", result.get_data(as_text=True))
        self.assertNotIn("Traceback", result.get_data(as_text=True))

    def test_trial_export_is_authenticated_and_available_in_read_only_mode(self):
        from app.modules.costing.trial_app import create_trial_app, initialize_trial

        directory = self.root / uuid4().hex / "trial"
        password = "transfer-test-password"
        initialize_trial(directory, password)
        app = create_trial_app(directory, read_only=True)
        record = app.extensions["costing_service"].create({"code": "READ-ONLY-EXPORT", "kind": "arm",
                    "items": [{"name": "材料", "amount": "20"}]}, actor="original")
        client = app.test_client()
        self.assertEqual(client.get("/costs/transfer/export", headers={"Accept": "application/json"}).status_code, 401)
        login_page = client.get("/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
        self.assertEqual(client.post("/login", data={"password": password, "csrf_token": csrf}).status_code, 302)
        result = client.get("/costs/transfer/export", headers={"Accept": "application/json"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(client.get("/costs/transfer/line-codes").status_code, 404)
        self.assertEqual(result.get_json()["records"], [record])
        self.assertEqual(result.get_json()["revisions"][0]["actor"], "original")
