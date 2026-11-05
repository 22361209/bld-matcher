from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from app.modules.costing import trial_app as trial_app_module
from app.modules.costing.trial_app import cost_money, create_trial_app, initialize_trial
from app.modules.costing.trial_support import CONFIG_NAME, DATABASE_NAME


PASSWORD = "costing-test-password-123"


def payload(code="KTESTLA"):
    return {"code": code, "kind": "arm", "items": [{"name": "衬套", "mode": "unit", "unit_price": "0.25",
                                                   "quantity": "3"}]}


def workbook_bytes():
    workbook = Workbook()
    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


class CostingTrialTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        initialize_trial(self.directory, PASSWORD)
        self.app = create_trial_app(self.directory)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary.cleanup()

    def csrf(self, client=None):
        client = client or self.client
        response = client.get("/login")
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def login(self, client=None):
        client = client or self.client
        response = client.post("/login", data={"password": PASSWORD, "csrf_token": self.csrf(client)})
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as state:
            # Login rotates the pre-authentication session; visit a form to obtain its new CSRF token.
            self.assertNotIn("_csrf_token", state)
        response = client.get("/costs/new")
        if response.status_code == 403:
            client.get("/login")
        with client.session_transaction() as state:
            return state["_csrf_token"]

    def post(self, path, data, token, client=None):
        return (client or self.client).post(path, json=data, headers={"X-CSRF-Token": token, "Accept": "application/json"})

    def test_authentication_and_csrf_are_enforced_even_in_testing(self):
        self.assertEqual(self.client.get("/", headers={"Accept": "application/json"}).status_code, 401)
        self.assertEqual(self.client.post("/login", data={"password": PASSWORD}).status_code, 400)
        token = self.login()
        self.assertEqual(self.post("/costs/new", payload(), "wrong").status_code, 400)
        self.assertEqual(self.post("/costs/new", payload(), token).status_code, 201)
        self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(self.client.get("/", headers={"Accept": "application/json"}).status_code, 401)

    def test_wrong_password_is_rate_limited(self):
        token = self.csrf()
        for _ in range(10):
            response = self.client.post("/login", data={"password": "wrong", "csrf_token": token})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/login", data={"password": PASSWORD, "csrf_token": token}).status_code, 429)

    def test_create_update_reopen_duplicate_and_conflict(self):
        token = self.login()
        raw = {**payload(), "actor": "forged", "source_info": {"forged": True}}
        result = self.post("/costs/new", raw, token).get_json()["record"]
        self.assertEqual(result["calculated_cost"], "0.75")
        self.assertEqual(result["source_info"], {})
        self.assertNotEqual(result["created_by"], "forged")
        self.assertEqual(self.post("/costs/new", raw, token).status_code, 409)
        result["items"][0]["unit_price"] = "0.30"
        updated = self.post(f"/costs/{result['id']}", result, token)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["record"]["calculated_cost"], "0.9")
        self.assertEqual(self.post(f"/costs/{result['id']}", result, token).status_code, 409)
        reloaded = self.client.get(f"/costs/{result['id']}", headers={"Accept": "application/json"}).get_json()["record"]
        self.assertEqual(reloaded["calculated_cost"], "0.9")
        self.assertEqual(len(self.app.extensions["costing_service"].revisions(result["id"])), 2)

    def test_preview_does_not_write_and_accepts_unnamed_draft(self):
        token = self.login()
        response = self.post("/costs/preview", payload(""), token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["record"]["code"], "")
        self.assertEqual(response.get_json()["record"]["status"], "draft")
        self.assertEqual(self.app.extensions["costing_service"].list()["total"], 0)

    def test_read_only_session_cannot_modify(self):
        read_only = create_trial_app(self.directory, read_only=True)
        client = read_only.test_client()
        token = self.login(client)
        self.assertEqual(client.get("/", headers={"Accept": "application/json"}).status_code, 200)
        self.assertEqual(self.post("/costs/new", payload(), token, client).status_code, 403)
        self.assertEqual(self.post("/costs/preview", payload(), token, client).status_code, 403)
        self.assertEqual(client.get("/import").status_code, 403)

    def test_copy_form_keeps_original_and_create_is_independent(self):
        token = self.login()
        original = self.post("/costs/new", payload(), token).get_json()["record"]
        response = self.client.get(f"/costs/new?copy={original['id']}")
        self.assertEqual(response.status_code, 200)
        editor = response.get_data(as_text=True)
        self.assertIn('data-list-url="/"', editor)
        self.assertIn('form="costing-editor" data-costing-leave-save', editor)
        copied = self.post("/costs/new", {**original, "code": "KTESTRA", "items": original["items"]}, token)
        self.assertEqual(copied.status_code, 201)
        self.assertNotEqual(copied.get_json()["record"]["items"][0]["id"], original["items"][0]["id"])
        self.assertEqual(self.app.extensions["costing_service"].get(original["id"])["version"], 1)

    def test_import_preview_is_bound_to_session_and_single_use(self):
        token = self.login()
        parsed = {"records": [payload("IMPORT1")], "warnings": [], "summary": {}, "references": {"cartons": []}}
        with patch("app.modules.costing.workbook_import.parse_cost_workbook", return_value=parsed):
            preview = self.client.post("/import/preview", data={"file": (workbook_bytes(), "cost.xlsx")},
                                       headers={"X-CSRF-Token": token, "Accept": "application/json"})
        self.assertEqual(preview.status_code, 200)
        preview_token = preview.get_json()["preview_token"]
        second = self.app.test_client()
        second_csrf = self.login(second)
        response = self.post("/import/apply", {"preview_token": preview_token}, second_csrf, second)
        self.assertEqual(response.status_code, 422)
        response = self.post("/import/apply", {"preview_token": preview_token}, token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["imported"], 1)
        self.assertEqual(self.post("/import/apply", {"preview_token": preview_token}, token).status_code, 409)
        self.assertEqual(list((self.directory / "previews").glob("*.json")), [])

    def test_import_rejects_invalid_archive_and_foreign_signature(self):
        token = self.login()
        response = self.client.post("/import/preview", data={"file": (io.BytesIO(b"bad"), "cost.xlsx")},
                                    headers={"X-CSRF-Token": token, "Accept": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.post("/import/apply", {"preview_token": "tampered"}, token).status_code, 422)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("readme.txt", "not xlsx")
        stream.seek(0)
        response = self.client.post("/import/preview", data={"file": (stream, "cost.xlsx")},
                                    headers={"X-CSRF-Token": token, "Accept": "application/json"})
        self.assertEqual(response.status_code, 422)

    def test_initialize_preserves_existing_credentials_and_data(self):
        before = (self.directory / CONFIG_NAME).read_bytes()
        self.assertEqual(initialize_trial(self.directory, "replacement-password"), {"created": False, "password": None})
        self.assertEqual((self.directory / CONFIG_NAME).read_bytes(), before)
        config = json.loads(before)
        self.assertNotIn(PASSWORD, json.dumps(config))
        self.assertEqual((self.directory / CONFIG_NAME).stat().st_mode & 0o777, 0o600)

    def test_existing_access_config_with_missing_database_requires_restore(self):
        before = (self.directory / CONFIG_NAME).read_bytes()
        database_path = self.directory / DATABASE_NAME
        database_path.unlink()
        with self.assertRaisesRegex(ValueError, "恢复已有成本库"):
            initialize_trial(self.directory, "replacement-password")
        self.assertFalse(database_path.exists())
        self.assertEqual((self.directory / CONFIG_NAME).read_bytes(), before)

    def test_failed_first_initialization_cleans_only_new_database(self):
        target = self.directory / "new-environment"
        # Web fixtures may reload app.*; patch the module captured with these functions.
        with patch.object(trial_app_module, "atomic_json", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                initialize_trial(target, PASSWORD)
        self.assertFalse((target / DATABASE_NAME).exists())
        self.assertFalse((target / CONFIG_NAME).exists())
        self.assertTrue(initialize_trial(target, PASSWORD)["created"])

    def test_host_and_response_headers(self):
        self.assertEqual(self.client.get("/health/ready", base_url="http://evil.example").status_code, 400)
        self.app.config["COSTING_TRIAL_INSTANCE_ID"] = "test-instance"
        response = self.client.get("/health/ready")
        self.assertEqual(response.get_json()["instance_id"], "test-instance")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(cost_money("19.783652"), "19.783652")
        self.assertEqual(cost_money("1.33333333333333333333333333333"), "1.333333")
        self.assertEqual(cost_money(None), "—")


if __name__ == "__main__":
    unittest.main()
