from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, g

from app.modules.quotes import drawings_web as quotes_drawings_web_module
from app.modules.quotes import web as quotes_web_module
from app.modules.quotes.domain import DrawingFileReference, QuoteDrawingLink, QuoteDrawingLinkView
from app.modules.quotes.web import quote_web
from app.security import can, csrf_field


ROOT = Path(__file__).resolve().parents[1]


def _route_app(permissions: set[str]) -> Flask:
    app = Flask(__name__, template_folder=ROOT / "templates")
    app.jinja_env.globals["can"] = can
    app.jinja_env.globals["csrf_field"] = csrf_field
    app.config.update(TESTING=True, SECRET_KEY="test")

    @app.before_request
    def load_test_user() -> None:
        g.user = {"username": "tester", "role": "viewer", "permissions": permissions}

    app.add_url_rule("/", endpoint="index", view_func=lambda: "index")
    app.add_url_rule(
        "/customers/<int:customer_id>/drawings/files/<int:file_id>/download",
        endpoint="download_customer_drawing_file",
        view_func=lambda customer_id, file_id: "download",
    )
    app.add_url_rule(
        "/customers/<int:customer_id>/drawings/files/<int:file_id>/preview",
        endpoint="preview_customer_drawing_file",
        view_func=lambda customer_id, file_id: "preview",
    )
    app.register_blueprint(quote_web)
    return app


def _record(**overrides) -> SimpleNamespace:
    values = {
        "id": 7,
        "customer_id": 1,
        "customer_name": "Module Customer",
        "bld_no": "MODULE-001",
        "product_model": "MODULE-001",
        "customer_product_code": "",
        "tax_price": 10.25,
        "net_price": None,
        "currency": "USD",
        "quote_date": "2026-08-01",
        "quoted_by": "tester",
        "remark": "",
        "attachment_path": "",
        "quote_no": "Q260801001",
        "version": 1,
    }
    return SimpleNamespace(**{**values, **overrides})


def _link_view(*, version_no: int, current_version: int) -> QuoteDrawingLinkView:
    return QuoteDrawingLinkView(
        link=QuoteDrawingLink(
            id=21,
            quote_record_id=7,
            drawing_file_id=11,
            created_by="tester",
            created_at="2026-08-01 00:00:00",
        ),
        file=DrawingFileReference(
            file_id=11,
            customer_id=1,
            group_id=1,
            direction="customer",
            direction_label="客户来图",
            title="支架图纸",
            version_no=version_no,
            revision_label="Rev A",
            original_name="bracket-v1.pdf",
            current_version=current_version,
            group_archived=False,
            previewable=True,
        ),
    )


def _detail_service(**overrides) -> SimpleNamespace:
    values = {
        "records_by_quote_no": lambda quote_no: [_record()],
        "contract_documents_by_quote_no": lambda quote_no: [],
        "drawing_links_by_quote_no": lambda quote_no: {7: [_link_view(version_no=1, current_version=2)]},
        "drawing_link_options_by_quote_no": lambda quote_no: {
            7: [
                {
                    "group_id": 1,
                    "direction_label": "客户来图",
                    "title": "支架图纸",
                    "current_version": 2,
                    "versions": [_link_view(version_no=2, current_version=2).file],
                }
            ]
        },
    }
    return SimpleNamespace(**{**values, **overrides})


class QuoteDrawingRouteTest(unittest.TestCase):
    def test_link_requires_edit_permission(self) -> None:
        app = _route_app({"view_customer_prices"})
        calls = []
        service = SimpleNamespace(link_drawing=lambda *args, **kwargs: calls.append((args, kwargs)))
        with patch.object(quotes_drawings_web_module, "get_quote_service", return_value=service):
            response = app.test_client().post("/quotes/7/drawings/link", data={"drawing_file_id": "11"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))
        self.assertEqual(calls, [])

    def test_link_calls_service_and_redirects_back(self) -> None:
        app = _route_app({"edit_customer_prices"})
        calls = []

        def link_drawing(quote_id, drawing_file_id, *, actor):
            calls.append((quote_id, drawing_file_id, actor))
            return _record()

        service = SimpleNamespace(link_drawing=link_drawing)
        with patch.object(quotes_drawings_web_module, "get_quote_service", return_value=service):
            response = app.test_client().post("/quotes/7/drawings/link", data={"drawing_file_id": "11"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/quotes", response.headers["Location"])
        self.assertEqual(calls, [(7, "11", "tester")])

    def test_unlink_requires_edit_permission(self) -> None:
        app = _route_app({"view_customer_prices"})
        calls = []
        service = SimpleNamespace(unlink_drawing=lambda *args, **kwargs: calls.append((args, kwargs)))
        with patch.object(quotes_drawings_web_module, "get_quote_service", return_value=service):
            response = app.test_client().post("/quotes/7/drawings/21/unlink")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))
        self.assertEqual(calls, [])

    def test_unlink_calls_service_and_redirects_back(self) -> None:
        app = _route_app({"edit_customer_prices"})
        calls = []

        def unlink_drawing(quote_id, link_id, *, actor):
            calls.append((quote_id, link_id, actor))
            return _record()

        service = SimpleNamespace(unlink_drawing=unlink_drawing)
        with patch.object(quotes_drawings_web_module, "get_quote_service", return_value=service):
            response = app.test_client().post("/quotes/7/drawings/21/unlink")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/quotes", response.headers["Location"])
        self.assertEqual(calls, [(7, 21, "tester")])


class QuoteNumberDetailRenderTest(unittest.TestCase):
    @staticmethod
    def _render(permissions: set[str], service: SimpleNamespace) -> str:
        app = _route_app(permissions)
        with patch.object(quotes_web_module, "get_quote_service", return_value=service):
            response = app.test_client().get("/quotes/number/Q260801001")
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_detail_renders_linked_drawing_and_newer_badge(self) -> None:
        html = self._render({"view_customer_prices", "view_customers"}, _detail_service())
        self.assertIn("客户来图", html)
        self.assertIn("支架图纸", html)
        self.assertIn("V1", html)
        self.assertIn("Rev A", html)
        self.assertIn("bracket-v1.pdf", html)
        self.assertIn("已有 V2 新版", html)
        self.assertIn("/customers/1/drawings/files/11/download", html)
        self.assertIn("/customers/1/drawings/files/11/preview", html)

    def test_detail_hides_newer_badge_when_link_is_current(self) -> None:
        service = _detail_service(
            drawing_links_by_quote_no=lambda quote_no: {7: [_link_view(version_no=2, current_version=2)]}
        )
        html = self._render({"view_customer_prices"}, service)
        self.assertNotIn("已有 V2 新版", html)
        self.assertIn("支架图纸", html)

    def test_detail_without_view_customers_shows_text_without_file_links(self) -> None:
        html = self._render({"view_customer_prices"}, _detail_service())
        self.assertIn("支架图纸", html)
        self.assertNotIn("/customers/1/drawings/files/11/download", html)
        self.assertNotIn("/customers/1/drawings/files/11/preview", html)

    def test_detail_renders_link_panel_only_for_editors(self) -> None:
        viewer_html = self._render({"view_customer_prices"}, _detail_service())
        self.assertNotIn("关联图纸版本", viewer_html)
        self.assertNotIn("quote-drawing-link-7", viewer_html)

        editor_html = self._render({"view_customer_prices", "edit_customer_prices"}, _detail_service())
        self.assertIn("关联图纸版本", editor_html)
        self.assertIn('form="quote-drawing-link-7"', editor_html)
        self.assertIn('form="quote-drawing-unlink-21"', editor_html)
        self.assertIn('<optgroup label="客户来图 · 支架图纸">', editor_html)
        self.assertIn('action="/quotes/7/drawings/link"', editor_html)
        self.assertIn('action="/quotes/7/drawings/21/unlink"', editor_html)


if __name__ == "__main__":
    unittest.main()
