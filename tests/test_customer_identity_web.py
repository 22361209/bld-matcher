from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, g

from app.modules.customers import identity_web, web


class CustomerIdentityRouteTest(unittest.TestCase):
    @staticmethod
    def _route_app(permissions: set[str]) -> Flask:
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="test")

        @app.before_request
        def load_test_user() -> None:
            g.user = {"username": "tester", "role": "viewer", "permissions": permissions}

        app.add_url_rule("/", endpoint="index", view_func=lambda: "index")
        web.register(app)
        identity_web.register(app)
        return app

    def test_legacy_save_route_cannot_update_an_existing_customer(self) -> None:
        app = self._route_app({"add_customers", "edit_customers"})
        service = SimpleNamespace(create=lambda *args, **kwargs: self.fail("must not create"))
        with patch.object(web, "get_customer_service", return_value=service):
            response = app.test_client().post(
                "/customers/save",
                data={"id": "1", "name": "越权修改", "code": "C-9"},
                headers={"Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"ok": False, "error": "客户名称、客户编号和负责人请使用档案中的对应维护入口。"},
        )

    def test_owner_update_uses_regular_customer_edit_permission_only(self) -> None:
        app = self._route_app({"edit_customers"})
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def update_owner(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(id=1, name="测试客户")

        service = SimpleNamespace(update_owner=update_owner)
        with patch.object(identity_web, "get_customer_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/owner",
                data={"owner_username": "008"},
                headers={"Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "customer": {"id": 1, "name": "测试客户"}})
        self.assertEqual(calls, [((1, "008"), {"actor": "tester"})])

    def test_identity_routes_require_the_dedicated_permission(self) -> None:
        denied_app = self._route_app({"edit_customers"})
        denied_service = SimpleNamespace()
        with patch.object(identity_web, "get_customer_service", return_value=denied_service):
            denied = denied_app.test_client().post(
                "/customers/1/identity/name",
                data={"name": "新客户", "reason": "名称调整"},
                headers={"Accept": "application/json"},
            )
        self.assertEqual(denied.status_code, 403)

        app = self._route_app({"change_customer_identity"})
        calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

        def rename(*args, **kwargs):
            calls.append(("name", args, kwargs))
            return SimpleNamespace(id=1, name="新客户")

        def update_code(*args, **kwargs):
            calls.append(("code", args, kwargs))
            return SimpleNamespace(id=1, name="新客户")

        service = SimpleNamespace(rename=rename, update_code=update_code)
        with patch.object(identity_web, "get_customer_service", return_value=service):
            client = app.test_client()
            name_response = client.post(
                "/customers/1/identity/name",
                data={"name": "新客户", "reason": "客户合同主体更新"},
                headers={"Accept": "application/json"},
            )
            code_response = client.post(
                "/customers/1/identity/code",
                data={"code": "C-009", "reason": "统一编码"},
                headers={"Accept": "application/json"},
            )
        self.assertEqual(name_response.status_code, 200)
        self.assertEqual(code_response.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("name", (1, "新客户"), {"reason": "客户合同主体更新", "actor": "tester"}),
                ("code", (1, "C-009"), {"reason": "统一编码", "actor": "tester"}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
