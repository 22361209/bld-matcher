from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from flask import Blueprint, Flask
from pydantic import Field

from app.database import connect
from app.platform.api_auth import api_scope_required
from app.platform.api_errors import register_api_error_handlers, success_response
from app.platform.api_keys import (
    create_internal_api_key,
    internal_api_key_status,
    list_internal_api_keys,
    verify_internal_api_token,
)
from app.platform.api_schemas import StrictApiModel, api_schema
from app.platform.idempotency import _claim, idempotency_required
from app.platform.request_context import register_request_context


class MutationRequest(StrictApiModel):
    value: int = Field(gt=0)
    on_behalf_of: str = ""


class ApiPlatformTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "platform.sqlite3"
        with connect(self.db_path):
            pass
        self.calls = 0

        api = Blueprint("platform_test_api", __name__)
        register_api_error_handlers(api)

        @api.post("/api/v1/test-mutations")
        @api_scope_required("quotes:write")
        @idempotency_required
        @api_schema(MutationRequest)
        def mutate(*, payload: MutationRequest):
            self.calls += 1
            return success_response({"call": self.calls, "value": payload.value}, status=201)

        @api.get("/api/v1/test-failure")
        @api_scope_required("api:read")
        def fail():
            raise RuntimeError("secret-local-path:/tmp/private.sqlite3")

        self.app = Flask(__name__)
        self.app.config["TESTING"] = False
        register_request_context(self.app)
        self.app.register_blueprint(api)
        self.client = self.app.test_client()
        self.auth_patch = patch.multiple(
            "app.platform.api_auth",
            DB_PATH=self.db_path,
            INTERNAL_API_TOKEN="",
        )
        self.idempotency_patch = patch("app.platform.idempotency.DB_PATH", self.db_path)
        self.auth_patch.start()
        self.idempotency_patch.start()

    def tearDown(self):
        self.idempotency_patch.stop()
        self.auth_patch.stop()
        self.tmp.cleanup()

    def _token(self, *, scopes=None, expires_at="") -> str:
        with connect(self.db_path) as conn:
            return create_internal_api_key(
                conn,
                actor="tester",
                name="Platform Test",
                scopes=scopes,
                expires_at=expires_at,
            )

    @staticmethod
    def _headers(token: str, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", **extra}

    def test_scope_and_expiry_are_enforced(self):
        read_token = self._token()
        denied = self.client.post(
            "/api/v1/test-mutations",
            json={"value": 1},
            headers=self._headers(read_token, **{"Idempotency-Key": "scope-denied-001"}),
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json()["error"]["code"], "auth.insufficient_scope")

        expired_token = self._token(scopes=["quotes:write"])
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE internal_api_keys SET expires_at = '2000-01-01 23:59:59' WHERE name = 'Platform Test'"
            )
            conn.commit()
            status = internal_api_key_status(conn)
            key = list_internal_api_keys(conn)[0]
        expired = self.client.post(
            "/api/v1/test-mutations",
            json={"value": 1},
            headers=self._headers(expired_token, **{"Idempotency-Key": "expired-key-001"}),
        )
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(expired.get_json()["error"]["code"], "auth.unauthorized")
        self.assertFalse(status["enabled"])
        self.assertTrue(key["expired"])
        self.assertFalse(key["usable"])

    def test_api_key_rotation_reminder_is_advisory(self):
        token = self._token(scopes=["api:read"])
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE internal_api_keys SET created_at = '2026-01-01 00:00:00' WHERE name = 'Platform Test'"
            )
            conn.commit()
            before_due = list_internal_api_keys(
                conn,
                rotation_days=90,
                current_time=datetime(2026, 3, 31, 23, 59, 59),
            )[0]
            due = list_internal_api_keys(
                conn,
                rotation_days=90,
                current_time=datetime(2026, 4, 1),
            )[0]
            principal = verify_internal_api_token(conn, token)
        self.assertFalse(before_due["rotation_due"])
        self.assertTrue(due["rotation_due"])
        self.assertEqual(due["rotation_due_at"], "2026-04-01")
        self.assertTrue(due["usable"], "rotation reminder must not disable the key")
        self.assertIsNotNone(principal)

    def test_idempotency_replays_response_and_audits_server_principal(self):
        token = self._token(scopes=["quotes:write"])
        headers = self._headers(
            token,
            **{
                "Idempotency-Key": "repeatable-request-001",
                "X-Request-ID": "consumer-request-42",
            },
        )
        first = self.client.post(
            "/api/v1/test-mutations",
            json={"value": 1, "on_behalf_of": "sales-user"},
            headers=headers,
        )
        second = self.client.post(
            "/api/v1/test-mutations",
            json={"value": 1, "on_behalf_of": "sales-user"},
            headers=headers,
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.get_json(), second.get_json())
        self.assertEqual(second.headers["Idempotency-Replayed"], "true")
        self.assertEqual(first.headers["X-Request-ID"], "consumer-request-42")
        self.assertEqual(self.calls, 1)
        with connect(self.db_path) as conn:
            stored = conn.execute("SELECT state, response_status FROM api_idempotency_keys").fetchone()
            audit = conn.execute(
                "SELECT actor, detail FROM audit_logs WHERE action = 'API mutation'"
            ).fetchone()
        self.assertEqual(dict(stored), {"state": "completed", "response_status": 201})
        self.assertEqual(audit["actor"], "Platform Test")
        self.assertIn('"request_id": "consumer-request-42"', audit["detail"])
        self.assertIn('"on_behalf_of": "sales-user"', audit["detail"])

    def test_idempotency_rejects_missing_or_reused_key(self):
        token = self._token(scopes=["quotes:write"])
        missing = self.client.post(
            "/api/v1/test-mutations",
            json={"value": 1},
            headers=self._headers(token),
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.get_json()["error"]["code"], "idempotency.required")

        headers = self._headers(token, **{"Idempotency-Key": "conflicting-request-001"})
        accepted = self.client.post("/api/v1/test-mutations", json={"value": 1}, headers=headers)
        conflict = self.client.post("/api/v1/test-mutations", json={"value": 2}, headers=headers)
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["error"]["code"], "idempotency.conflict")

    def test_concurrent_idempotency_claim_has_one_owner(self):
        barrier = Barrier(6)

        def claim_once() -> str:
            barrier.wait()
            with connect(self.db_path) as conn:
                return _claim(
                    conn,
                    principal_id="key:concurrent",
                    endpoint="api_v1.concurrent",
                    method="POST",
                    key="concurrent-request-001",
                    request_hash="fixed-request-hash",
                ).state

        with ThreadPoolExecutor(max_workers=6) as executor:
            states = list(executor.map(lambda _index: claim_once(), range(6)))
        self.assertEqual(states.count("claimed"), 1)
        self.assertEqual(states.count("in_progress"), 5)

    def test_schema_validation_rejects_unknown_and_invalid_fields(self):
        token = self._token(scopes=["quotes:write"])
        headers = self._headers(token, **{"Idempotency-Key": "schema-invalid-001"})
        response = self.client.post(
            "/api/v1/test-mutations",
            json={"value": 0, "unexpected": True},
            headers=headers,
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "request.invalid")
        paths = {item["path"] for item in payload["error"]["details"]["fields"]}
        self.assertEqual(paths, {"value", "unexpected"})
        with connect(self.db_path) as conn:
            pending = conn.execute("SELECT COUNT(*) FROM api_idempotency_keys").fetchone()[0]
        self.assertEqual(pending, 0)

    def test_unexpected_errors_use_stable_envelope_without_exception_text(self):
        token = self._token(scopes=["api:read"])
        response = self.client.get(
            "/api/v1/test-failure",
            headers=self._headers(token, **{"X-Request-ID": "failure-request-1"}),
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"]["code"], "internal.unexpected")
        self.assertEqual(response.get_json()["request_id"], "failure-request-1")
        self.assertNotIn("secret-local-path", body)
        self.assertNotIn("private.sqlite3", body)

    def test_principal_is_typed_and_default_key_is_read_only(self):
        token = self._token()
        with connect(self.db_path) as conn:
            principal = verify_internal_api_token(conn, token)
        self.assertIsNotNone(principal)
        self.assertEqual(principal.integration_name, "Platform Test")
        self.assertIn("api:read", principal.scopes)
        self.assertIn("quotes:read", principal.scopes)
        self.assertNotIn("quotes:write", principal.scopes)


if __name__ == "__main__":
    unittest.main()
