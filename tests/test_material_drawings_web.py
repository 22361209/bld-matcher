from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g, render_template
from jinja2 import FileSystemLoader

from app.modules.materials import web as materials_web


class MaterialDrawingsRoutePermissionTest(unittest.TestCase):
    @staticmethod
    def _route_app(permissions: set[str]) -> Flask:
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="test")

        @app.before_request
        def load_test_user() -> None:
            g.user = {"username": "tester", "role": "viewer", "permissions": permissions}

        app.add_url_rule("/", endpoint="index", view_func=lambda: "index")
        materials_web.register(app)
        return app

    def test_list_preview_and_download_require_view_permission(self) -> None:
        app = self._route_app(set())
        client = app.test_client()

        with patch.object(materials_web, "get_material_service") as service_factory:
            for path in (
                "/material-drawings",
                "/material-drawings/preview/QD1000.pdf",
                "/material-drawings/QD1000.pdf",
            ):
                with self.subTest(path=path):
                    response = client.get(path, headers={"Accept": "application/json"})
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.get_json(), {"ok": False, "error": "当前账号没有权限执行这个操作。"})
            service_factory.assert_not_called()

    def test_view_permission_does_not_grant_upload_permission(self) -> None:
        app = self._route_app({"view_material_drawings"})

        with patch.object(materials_web, "get_material_service") as service_factory:
            response = app.test_client().post(
                "/material-drawings/upload",
                data={"drawing": (io.BytesIO(b"%PDF-1.4\\n"), "QD1000.pdf")},
                content_type="multipart/form-data",
                headers={"Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"ok": False, "error": "当前账号没有权限执行这个操作。"})
        service_factory.assert_not_called()


class MaterialDrawingsNavigationPermissionTest(unittest.TestCase):
    @staticmethod
    def _render_nav(permissions: set[str]) -> str:
        templates_dir = Path(__file__).resolve().parent.parent / "templates"
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="test")
        app.jinja_loader = FileSystemLoader(str(templates_dir))
        app.jinja_env.globals["can"] = lambda permission: permission in permissions
        app.jinja_env.globals["csrf_field"] = lambda: ""
        app.jinja_env.globals["ROLE_LABELS"] = {}
        app.add_url_rule("/", endpoint="index", view_func=lambda: "index")
        app.add_url_rule("/products", endpoint="products", view_func=lambda: "products")
        app.add_url_rule("/tubes", endpoint="tube_items", view_func=lambda: "tubes")
        app.add_url_rule("/materials", endpoint="materials", view_func=lambda: "materials")
        app.add_url_rule("/materials/items", endpoint="material_items", view_func=lambda: "material items")
        app.add_url_rule(
            "/material-drawings",
            endpoint="material_drawings",
            view_func=lambda: "material drawings",
        )
        app.add_url_rule("/account/password", endpoint="change_password", view_func=lambda: "password")
        app.add_url_rule("/logout", endpoint="logout", view_func=lambda: "logout", methods=["POST"])

        with app.test_request_context("/"):
            g.user = {"username": "tester", "role": "viewer", "permissions": permissions}
            return render_template("_nav.html", active_page="")

    def test_nav_hides_material_drawings_without_view_permission(self) -> None:
        hidden = self._render_nav(set())
        visible = self._render_nav({"view_material_drawings"})

        self.assertNotIn('href="/material-drawings">物料图纸</a>', hidden)
        self.assertIn('href="/material-drawings">物料图纸</a>', visible)
