from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, g

from app.modules.customer_drawings.domain import CustomerDrawingValidationError
from app.modules.customer_drawings.ports import CustomerFilePayload
from app.modules.customers import drawings_web


class CustomerDrawingRouteTest(unittest.TestCase):
    @staticmethod
    def _route_app(permissions: set[str]) -> Flask:
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="test")

        @app.before_request
        def load_test_user() -> None:
            g.user = {"username": "tester", "role": "viewer", "permissions": permissions}

        app.add_url_rule("/", endpoint="index", view_func=lambda: "index")
        app.add_url_rule("/customers/<int:customer_id>", endpoint="customer_detail", view_func=lambda customer_id: "detail")
        drawings_web.register(app)
        return app

    def test_create_requires_edit_permission(self) -> None:
        app = self._route_app({"view_customers"})
        service = SimpleNamespace(create=lambda *args, **kwargs: None)
        with patch.object(drawings_web, "get_customer_drawing_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/drawings",
                data={"direction": "customer", "title": "支架图纸"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_create_calls_service_and_redirects_to_drawings_view(self) -> None:
        app = self._route_app({"edit_customers"})
        calls = []

        def create(customer_id, form, *, files, actor):
            calls.append((customer_id, dict(form), files, actor))

        service = SimpleNamespace(create=create)
        with patch.object(drawings_web, "get_customer_drawing_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/drawings",
                data={"direction": "customer", "title": "支架图纸", "bld_no": "K8053", "drawing_no": "C-1"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=drawings", response.headers["Location"])
        self.assertEqual(calls[0][0], 1)
        self.assertEqual(calls[0][1]["title"], "支架图纸")
        self.assertEqual(calls[0][3], "tester")

    def test_archive_requires_delete_permission(self) -> None:
        app = self._route_app({"edit_customers"})
        service = SimpleNamespace(archive=lambda *args, **kwargs: None)
        with patch.object(drawings_web, "get_customer_drawing_service", return_value=service):
            response = app.test_client().post("/customers/1/drawings/2/archive")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

        app = self._route_app({"delete_customers"})
        calls = []
        service = SimpleNamespace(archive=lambda *args, **kwargs: calls.append((args, kwargs)))
        with patch.object(drawings_web, "get_customer_drawing_service", return_value=service):
            response = app.test_client().post("/customers/1/drawings/2/archive")
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=drawings", response.headers["Location"])
        self.assertEqual(calls[0][0], (1, 2))
        self.assertEqual(calls[0][1]["actor"], "tester")

    def test_upload_version_passes_revision_label_and_note(self) -> None:
        app = self._route_app({"edit_customers"})
        calls = []
        service = SimpleNamespace(add_version=lambda *args, **kwargs: calls.append((args, kwargs)))
        with patch.object(drawings_web, "get_customer_drawing_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/drawings/2/versions",
                data={
                    "revision_label": "Rev B",
                    "note": "按客户意见修改",
                    "files": (io.BytesIO(b"\x89PNG\r\n\x1a\npayload"), "v2.png"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        args, kwargs = calls[0]
        self.assertEqual(args[0:2], (1, 2))
        self.assertEqual(len(args[2]), 1)
        self.assertEqual(kwargs["revision_label"], "Rev B")
        self.assertEqual(kwargs["note"], "按客户意见修改")
        self.assertEqual(kwargs["actor"], "tester")

    def test_download_sets_private_headers_and_attachment(self) -> None:
        app = self._route_app({"view_customers"})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "drawing.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\npayload")
            payload = CustomerFilePayload(
                path=path,
                download_name="支架-v1.png",
                content_type="image/png",
                size_bytes=path.stat().st_size,
                sha256="",
                previewable=True,
            )
            service = SimpleNamespace(file_payload=lambda *args, **kwargs: payload)
            with patch.object(drawings_web, "get_customer_drawing_service", return_value=service):
                response = app.test_client().get("/customers/1/drawings/files/9/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.data, b"\x89PNG\r\n\x1a\npayload")

    def test_preview_validation_failure_redirects_with_flash(self) -> None:
        app = self._route_app({"view_customers"})

        def file_payload(*args, **kwargs):
            raise CustomerDrawingValidationError(
                "customer_drawing.preview_not_supported", "该文件格式不支持在线预览，请下载查看。"
            )

        service = SimpleNamespace(file_payload=file_payload)
        with patch.object(drawings_web, "get_customer_drawing_service", return_value=service):
            response = app.test_client().get("/customers/1/drawings/files/9/preview")
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=drawings", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
