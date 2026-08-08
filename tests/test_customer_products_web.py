from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, g

from app.modules.customer_products.domain import (
    CUSTOMER_DRAWING_KINDS,
    CatalogProductInfo,
    CustomerDrawingFile,
    CustomerDrawingSlot,
    CustomerProduct,
    CustomerProductDeletionResult,
    CustomerProductValidationError,
)
from app.modules.customer_products.ports import CustomerFilePayload
from app.modules.customers import drawings_web, products_web


def _drawing_file(
    file_id: int = 11,
    version_no: int = 1,
    revision_label: str = "Rev A",
    content_type: str = "application/pdf",
) -> CustomerDrawingFile:
    return CustomerDrawingFile(
        id=file_id,
        sync_id="f" * 32,
        group_id=5,
        version_no=version_no,
        revision_label=revision_label,
        original_name=f"drawing-v{version_no}.pdf",
        storage_path="stored/path.pdf",
        content_type=content_type,
        size_bytes=128,
        created_at=f"2026-08-0{version_no} 10:00:00",
    )


def _slot(
    kind: str = "bld",
    current_version: int = 2,
    files: tuple[CustomerDrawingFile, ...] | None = None,
) -> CustomerDrawingSlot:
    if files is None:
        files = (
            _drawing_file(file_id=11, version_no=1, revision_label="Rev A"),
            _drawing_file(file_id=12, version_no=2, revision_label="Rev B"),
        )
    return CustomerDrawingSlot(
        id=5,
        customer_product_id=7,
        customer_id=1,
        sync_id="s" * 32,
        kind=kind,
        current_version=current_version,
        created_by="tester",
        updated_by="tester",
        created_at="2026-08-01 10:00:00",
        updated_at="2026-08-07 10:00:00",
        files=files,
    )


def _product(
    drawings: tuple[CustomerDrawingSlot, ...] = (),
    catalog: CatalogProductInfo | None = None,
) -> CustomerProduct:
    return CustomerProduct(
        id=7,
        customer_id=1,
        sync_id="p" * 32,
        bld_no="K8053",
        customer_product_code="CUST-001",
        customer_product_name="支架总成",
        created_by="tester",
        updated_by="tester",
        created_at="2026-08-01 10:00:00",
        updated_at="2026-08-07 10:00:00",
        drawings=drawings,
        catalog=catalog,
    )


class CustomerProductRouteTest(unittest.TestCase):
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
        products_web.register(app)
        return app

    def test_create_requires_edit_permission(self) -> None:
        app = self._route_app({"view_customers"})
        service = SimpleNamespace(create=lambda *args, **kwargs: None)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post("/customers/1/products", data={"bld_no": "K8053"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_create_calls_service_and_redirects_to_products_view(self) -> None:
        app = self._route_app({"edit_customers"})
        calls = []

        def create(*args, **kwargs):
            calls.append((args, kwargs))
            return _product()

        service = SimpleNamespace(create=create)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products",
                data={
                    "bld_no": "k8053",
                    "customer_product_code": "CUST-001",
                    "customer_product_name": "支架总成",
                    "customer_drawing_revision_label": "Rev A",
                    "customer_drawing_file": (io.BytesIO(b"%PDF-1.4\nx"), "客户图纸.pdf"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/customers/1", response.headers["Location"])
        self.assertIn("view=products", response.headers["Location"])
        args, kwargs = calls[0]
        self.assertEqual(args, (1, "k8053", "CUST-001", "支架总成"))
        self.assertEqual(len(kwargs["customer_drawing_files"]), 1)
        self.assertEqual(kwargs["customer_drawing_files"][0].filename, "客户图纸.pdf")
        self.assertEqual(kwargs["customer_drawing_revision_label"], "Rev A")
        self.assertEqual(kwargs["actor"], "tester")

    def test_create_validation_error_flashes_and_redirects(self) -> None:
        app = self._route_app({"edit_customers"})

        def create(*args, **kwargs):
            raise CustomerProductValidationError(
                "customer_product.bld_not_quoted",
                "该 BLD 号未出现在该客户的报价历史中，不能建立客户商品。",
            )

        service = SimpleNamespace(create=create)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post("/customers/1/products", data={"bld_no": "K9999"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=products", response.headers["Location"])

    def test_update_never_passes_bld_no(self) -> None:
        app = self._route_app({"edit_customers"})
        calls = []

        def update(*args, **kwargs):
            calls.append((args, kwargs))
            return _product()

        service = SimpleNamespace(update=update)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/update",
                data={"bld_no": "HACKED", "customer_product_code": "C-9", "customer_product_name": "新名称"},
            )
        self.assertEqual(response.status_code, 302)
        args, kwargs = calls[0]
        self.assertEqual(args, (1, 7, "C-9", "新名称"))
        self.assertEqual(kwargs["actor"], "tester")

    def test_update_requires_edit_permission(self) -> None:
        app = self._route_app({"view_customers"})
        service = SimpleNamespace(update=lambda *args, **kwargs: None)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/update",
                data={"customer_product_code": "C-9", "customer_product_name": "新名称"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_delete_requires_delete_permission(self) -> None:
        app = self._route_app({"edit_customers"})
        service = SimpleNamespace(delete=lambda *args, **kwargs: None)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post("/customers/1/products/7/delete")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_delete_restore_incomplete_error_is_visible_to_json_client(self) -> None:
        app = self._route_app({"delete_customers"})

        def delete(*args, **kwargs):
            raise CustomerProductValidationError(
                "customer_drawing.restore_incomplete",
                "图纸文件暂存失败，且有 1 个文件恢复不完整，请联系管理员处理。",
            )

        service = SimpleNamespace(delete=delete)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/delete",
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("恢复不完整", payload["error"])
        self.assertIn("联系管理员", payload["error"])

    def test_delete_calls_service_and_reports_removed_files(self) -> None:
        app = self._route_app({"delete_customers"})
        calls = []

        def delete(*args, **kwargs):
            calls.append((args, kwargs))
            return CustomerProductDeletionResult(
                product=_product(drawings=(_slot("customer"),)),
                drawing_file_count=2,
            )

        service = SimpleNamespace(delete=delete)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/delete",
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "ok": True,
                "deleted_drawing_count": 2,
                "file_cleanup_complete": True,
                "file_cleanup_failed_count": 0,
                "post_commit_warning": False,
            },
        )
        self.assertEqual(calls[0][0], (1, 7))
        self.assertEqual(calls[0][1]["actor"], "tester")

    def test_delete_json_warns_when_physical_file_cleanup_is_incomplete(self) -> None:
        app = self._route_app({"delete_customers"})
        result = CustomerProductDeletionResult(
            product=_product(drawings=(_slot("customer"),)),
            drawing_file_count=2,
            cleanup_failure_count=1,
        )
        service = SimpleNamespace(delete=lambda *args, **kwargs: result)

        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/delete",
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["file_cleanup_complete"])
        self.assertEqual(payload["file_cleanup_failed_count"], 1)
        self.assertFalse(payload["post_commit_warning"])
        self.assertIn("未完成物理清理", payload["warning"])

    def test_delete_json_treats_post_commit_exit_error_as_success_with_warning(self) -> None:
        app = self._route_app({"delete_customers"})
        result = CustomerProductDeletionResult(
            product=_product(drawings=(_slot("customer"),)),
            drawing_file_count=2,
            post_commit_warning=True,
        )
        service = SimpleNamespace(delete=lambda *args, **kwargs: result)

        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/delete",
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["file_cleanup_complete"])
        self.assertTrue(payload["post_commit_warning"])
        self.assertIn("删除已经提交", payload["warning"])

    def test_delete_form_flashes_cleanup_warning_when_physical_cleanup_is_incomplete(self) -> None:
        app = self._route_app({"delete_customers"})
        result = CustomerProductDeletionResult(
            product=_product(drawings=(_slot("customer"),)),
            drawing_file_count=2,
            cleanup_failure_count=1,
        )
        service = SimpleNamespace(delete=lambda *args, **kwargs: result)
        client = app.test_client()

        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = client.post("/customers/1/products/7/delete")

        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            flashes = session.get("_flashes", [])
        self.assertEqual(len(flashes), 1)
        self.assertEqual(flashes[0][0], "error")
        self.assertIn("未完成物理清理", flashes[0][1])

    def test_upload_version_requires_edit_permission(self) -> None:
        app = self._route_app({"view_customers"})
        service = SimpleNamespace(upload_version=lambda *args, **kwargs: None)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/drawings/bld/versions",
                data={"revision_label": "Rev C"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_set_current_version_requires_edit_permission(self) -> None:
        app = self._route_app({"view_customers"})
        service = SimpleNamespace(set_current_version=lambda *args, **kwargs: None)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/drawings/customer/current",
                data={"version_no": "1"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_import_catalog_requires_edit_permission(self) -> None:
        app = self._route_app({"view_customers"})
        service = SimpleNamespace(import_catalog_drawing=lambda *args, **kwargs: None)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post("/customers/1/products/7/drawings/bld/import-catalog")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_upload_version_fetch_json_payload(self) -> None:
        app = self._route_app({"edit_customers"})
        calls = []

        def upload_version(*args, **kwargs):
            calls.append((args, kwargs))
            return _product(drawings=(_slot("bld", current_version=3),))

        service = SimpleNamespace(upload_version=upload_version)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/drawings/bld/versions",
                data={"revision_label": "Rev C", "files": (io.BytesIO(b"%PDF-1.4\nx"), "v3.pdf")},
                content_type="multipart/form-data",
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["version_no"], 3)
        args, kwargs = calls[0]
        self.assertEqual(args[0:3], (1, 7, "bld"))
        self.assertEqual(len(args[3]), 1)
        self.assertEqual(args[3][0].filename, "v3.pdf")
        self.assertEqual(kwargs["revision_label"], "Rev C")
        self.assertEqual(kwargs["actor"], "tester")

    def test_upload_rejects_unknown_kind(self) -> None:
        app = self._route_app({"edit_customers"})
        service = SimpleNamespace()
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post("/customers/1/products/7/drawings/other/versions", data={})
        self.assertEqual(response.status_code, 404)

    def test_set_current_version_fetch_json(self) -> None:
        app = self._route_app({"edit_customers"})
        calls = []

        def set_current_version(*args, **kwargs):
            calls.append((args, kwargs))
            return _product()

        service = SimpleNamespace(set_current_version=set_current_version)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/drawings/customer/current",
                data={"version_no": "1"},
                headers={"X-Requested-With": "fetch"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "version_no": 1})
        args, kwargs = calls[0]
        self.assertEqual(args, (1, 7, "customer", "1"))
        self.assertEqual(kwargs["actor"], "tester")

    def test_set_current_version_validation_error_returns_json_400(self) -> None:
        app = self._route_app({"edit_customers"})

        def set_current_version(*args, **kwargs):
            raise CustomerProductValidationError("customer_drawing.version_not_found", "指定版本不存在。")

        service = SimpleNamespace(set_current_version=set_current_version)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/drawings/bld/current",
                data={"version_no": "9"},
                headers={"X-Requested-With": "fetch"},
            )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("指定版本不存在", payload["error"])

    def test_import_catalog_drawing_fetch_json(self) -> None:
        app = self._route_app({"edit_customers"})
        calls = []

        def import_catalog_drawing(*args, **kwargs):
            calls.append((args, kwargs))
            return _product(drawings=(_slot("bld", current_version=3),))

        service = SimpleNamespace(import_catalog_drawing=import_catalog_drawing)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/drawings/bld/import-catalog",
                headers={"X-Requested-With": "fetch"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "version_no": 3})
        args, kwargs = calls[0]
        self.assertEqual(args, (1, 7))
        self.assertEqual(kwargs["actor"], "tester")

    def test_import_catalog_drawing_missing_returns_json_400(self) -> None:
        app = self._route_app({"edit_customers"})

        def import_catalog_drawing(*args, **kwargs):
            raise CustomerProductValidationError(
                "customer_product.catalog_drawing_missing", "产品目录中没有该 BLD 号的图纸，无法引入。"
            )

        service = SimpleNamespace(import_catalog_drawing=import_catalog_drawing)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().post(
                "/customers/1/products/7/drawings/bld/import-catalog",
                headers={"X-Requested-With": "fetch"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("无法引入", response.get_json()["error"])

    def test_versions_json_structure(self) -> None:
        app = self._route_app({"view_customers"})
        product = _product(
            drawings=(_slot("bld"),),
            catalog=CatalogProductInfo(bld_no="K8053", item_name="支架总成", has_drawing=True),
        )
        service = SimpleNamespace(
            list_for_customer=lambda customer_id: [product],
            kinds=lambda: CUSTOMER_DRAWING_KINDS,
        )
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().get("/customers/1/products/7/drawings/bld/versions.json")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "bld")
        self.assertEqual(payload["kind_label"], "BLD 图纸")
        self.assertEqual(payload["bld_no"], "K8053")
        self.assertEqual(payload["current_version"], 2)
        self.assertTrue(payload["catalog_has_drawing"])
        self.assertEqual([version["version_no"] for version in payload["versions"]], [2, 1])
        latest = payload["versions"][0]
        self.assertTrue(latest["is_current"])
        self.assertFalse(payload["versions"][1]["is_current"])
        self.assertEqual(latest["revision_label"], "Rev B")
        self.assertEqual(latest["file_id"], 12)
        self.assertTrue(latest["previewable"])
        self.assertEqual(latest["content_type"], "application/pdf")
        self.assertIn("/customers/1/drawings/files/12/preview", latest["preview_url"])
        self.assertIn("/customers/1/drawings/files/12/download", latest["download_url"])

    def test_versions_json_empty_slot(self) -> None:
        app = self._route_app({"view_customers"})
        service = SimpleNamespace(
            list_for_customer=lambda customer_id: [_product()],
            kinds=lambda: CUSTOMER_DRAWING_KINDS,
        )
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().get("/customers/1/products/7/drawings/customer/versions.json")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["versions"], [])
        self.assertEqual(payload["current_version"], 0)
        self.assertEqual(payload["kind_label"], "客户图纸")
        self.assertFalse(payload["catalog_has_drawing"])

    def test_versions_json_unknown_product_returns_404(self) -> None:
        app = self._route_app({"view_customers"})
        service = SimpleNamespace(list_for_customer=lambda customer_id: [], kinds=lambda: CUSTOMER_DRAWING_KINDS)
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().get("/customers/1/products/99/drawings/bld/versions.json")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["ok"])

    def test_versions_json_requires_view_permission(self) -> None:
        app = self._route_app(set())
        service = SimpleNamespace()
        with patch.object(products_web, "get_customer_product_service", return_value=service):
            response = app.test_client().get(
                "/customers/1/products/7/drawings/bld/versions.json",
                headers={"Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 403)


class CustomerProductsTabRenderTest(unittest.TestCase):
    """用最小 base.html 渲染真实 customer_detail.html，校验商品页签区块。"""

    BASE_STUB = (
        "<!doctype html><html><head>{% block page_head %}{% endblock %}</head>"
        "<body data-page=\"{% block page_id %}{% endblock %}\">"
        "{% block page_content %}{% endblock %}"
        "{% block page_modals %}{% endblock %}"
        "{% block page_scripts %}{% endblock %}"
        "</body></html>"
    )

    def _render(self, permissions: set[str], **overrides) -> str:
        from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
        from flask import render_template

        templates_dir = Path(__file__).resolve().parent.parent / "templates"
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="test")
        app.jinja_loader = ChoiceLoader(
            [DictLoader({"base.html": self.BASE_STUB}), FileSystemLoader(str(templates_dir))]
        )
        app.jinja_env.globals["can"] = lambda permission: permission in permissions
        app.jinja_env.globals["can_any"] = lambda *items: any(item in permissions for item in items)
        app.jinja_env.globals["csrf_field"] = lambda: ""
        app.jinja_env.globals["static_version"] = lambda filename: 0
        products_web.register(app)
        drawings_web.register(app)
        app.add_url_rule("/customers", endpoint="customers", view_func=lambda: "customers")
        app.add_url_rule(
            "/customers/<int:customer_id>", endpoint="customer_detail", view_func=lambda customer_id: "detail"
        )
        app.add_url_rule(
            "/customers/<int:customer_id>/status",
            endpoint="set_customer_status",
            methods=["POST"],
            view_func=lambda customer_id: "status",
        )
        app.add_url_rule(
            "/customers/<int:customer_id>/owner",
            endpoint="update_customer_owner",
            methods=["POST"],
            view_func=lambda customer_id: "owner",
        )
        app.add_url_rule(
            "/customers/<int:customer_id>/identity/name",
            endpoint="rename_customer",
            methods=["POST"],
            view_func=lambda customer_id: "name",
        )
        app.add_url_rule(
            "/customers/<int:customer_id>/identity/code",
            endpoint="update_customer_code",
            methods=["POST"],
            view_func=lambda customer_id: "code",
        )
        app.add_url_rule("/quotes", endpoint="quote_web.quotes", view_func=lambda: "quotes")

        context = {
            "customer": SimpleNamespace(
                id=1, name="测试客户", code="C-1", owner_username="007", status="active"
            ),
            "active_view": "products",
            "owners": [],
            "drawing_kinds": CUSTOMER_DRAWING_KINDS,
            "customer_products": [],
            "quoted_options": [],
        }
        context.update(overrides)
        with app.test_request_context("/customers/1?view=products"):
            g.user = {"username": "tester", "role": "viewer", "permissions": permissions}
            return render_template("customer_detail.html", **context)

    def test_products_tab_renders_columns_badge_and_thumb(self) -> None:
        product = _product(
            drawings=(_slot("bld", current_version=2),),
            catalog=CatalogProductInfo(
                bld_no="K8053",
                item_name="支架总成",
                image_url="/images/k8053.jpg",
                thumb_url="/images/k8053-thumb.jpg",
                has_drawing=True,
            ),
        )
        html = self._render(
            {"view_customers", "edit_customers", "delete_customers"},
            customer_products=[product],
            quoted_options=[SimpleNamespace(bld_no="K8053", customer_product_code="CUST-001")],
        )
        for heading in ("BLD号", "客户产品编码", "客户产品名称", "产品图片", "BLD 图纸", "客户图纸", "操作"):
            self.assertIn(heading, html)
        self.assertIn("K8053", html)
        self.assertIn("CUST-001", html)
        self.assertIn("支架总成", html)
        self.assertIn('src="/images/k8053-thumb.jpg"', html)
        self.assertIn("V2 · Rev B", html)
        self.assertIn("未上传", html)
        self.assertIn("data-open-drawing-modal", html)
        self.assertIn("data-open-customer-product-edit", html)
        self.assertIn("新增商品", html)
        self.assertIn(
            '<a class="active" href="/customers/1?view=products" aria-current="page">客户产品编码</a>',
            html,
        )
        self.assertIn(
            '<a class="linear-button subtle customer-back-link" href="/customers">← 返回客户列表</a>',
            html,
        )
        self.assertRegex(
            html,
            r'<div class="command-actions">\s*<a class="linear-button subtle customer-back-link"',
        )
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn('name="customer_drawing_file"', html)
        self.assertIn('tabindex="-1" aria-hidden="true" hidden data-customer-product-create-drawing-input', html)
        self.assertIn("data-customer-product-create-drawing-intake", html)
        self.assertIn("保存为客户图纸 V1", html)
        self.assertIn("/customers/1/products/7/delete", html)
        self.assertIn("全部历史版本将永久删除", html)
        self.assertIn("报价记录中的图纸关联也会一并清除", html)
        self.assertIn('value="K8053" data-code="CUST-001"', html)
        self.assertIn('data-catalog-has-drawing="true"', html)

    def test_products_tab_hides_edit_controls_for_viewer(self) -> None:
        product = _product(drawings=(_slot("bld"),))
        html = self._render({"view_customers"}, customer_products=[product])
        self.assertIn("V2 · Rev B", html)
        self.assertIn("data-open-drawing-modal", html)
        self.assertNotIn("<th>操作</th>", html)
        self.assertNotIn("新增商品", html)
        self.assertNotIn("data-open-customer-product-edit", html)
        self.assertNotIn("/customers/1/products/7/delete", html)
        self.assertNotIn("data-customer-product-modal", html)
        self.assertNotIn("data-drawing-upload", html)
        self.assertNotIn("data-drawing-set-current", html)
        self.assertNotIn("data-drawing-import-catalog", html)
        self.assertIn("data-customer-drawing-modal", html)

    def test_overview_keeps_customer_profile_and_hides_primary_contact_card(self) -> None:
        html = self._render(
            {"view_customers"},
            active_view="overview",
            contacts=[],
            summary=SimpleNamespace(
                quote_count=0,
                quoted_product_count=0,
                latest_quote_date="",
                file_count=0,
                primary_contact=None,
            ),
        )
        self.assertIn('class="data-section customer-profile-panel"', html)
        self.assertNotIn("customer-primary-contact-panel", html)
        self.assertNotIn("主要联系人", html)
        self.assertNotIn("管理联系人", html)

    def test_overview_makes_identity_read_only_and_uses_controlled_change_dialogs(self) -> None:
        summary = SimpleNamespace(
            quote_count=0,
            quoted_product_count=0,
            latest_quote_date="",
            file_count=0,
            primary_contact=None,
        )
        without_identity_permission = self._render(
            {"view_customers", "edit_customers"},
            active_view="overview",
            contacts=[],
            summary=summary,
        )
        self.assertIn('class="customer-profile-summary"', without_identity_permission)
        self.assertIn('action="/customers/1/owner"', without_identity_permission)
        self.assertNotIn("变更名称", without_identity_permission)
        self.assertNotIn('name="name" value="测试客户"', without_identity_permission)

        html = self._render(
            {"view_customers", "edit_customers", "change_customer_identity"},
            active_view="overview",
            contacts=[],
            summary=summary,
        )
        self.assertIn("变更名称", html)
        self.assertIn("变更编号", html)
        self.assertIn('action="/customers/1/identity/name"', html)
        self.assertIn('action="/customers/1/identity/code"', html)
        self.assertEqual(html.count('name="reason" required maxlength="500"'), 2)
        self.assertIn("客户名称和客户编号为受控资料", html)
        self.assertIn("pages/customer_detail.js", html)

    def test_products_tab_delete_only_permission_shows_delete_without_edit(self) -> None:
        product = _product(drawings=(_slot("customer"),))
        html = self._render(
            {"view_customers", "delete_customers"},
            customer_products=[product],
        )
        self.assertIn("<th>操作</th>", html)
        self.assertIn("/customers/1/products/7/delete", html)
        self.assertIn("全部历史版本将永久删除", html)
        self.assertNotIn("data-open-customer-product-edit", html)
        self.assertNotIn("data-customer-product-edit-modal", html)

    def test_products_tab_empty_state(self) -> None:
        html = self._render({"view_customers", "edit_customers"})
        self.assertIn("暂无客户商品，新增前请确认该商品已有报价记录。", html)
        self.assertIn("该客户暂无报价记录，无法新增商品", html)




class CustomerDrawingFileRouteTest(unittest.TestCase):
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

    def test_download_requires_view_permission(self) -> None:
        app = self._route_app(set())
        service = SimpleNamespace(file_payload=lambda *args, **kwargs: None)
        with patch.object(drawings_web, "get_customer_product_service", return_value=service):
            response = app.test_client().get("/customers/1/drawings/files/9/download")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

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
            with patch.object(drawings_web, "get_customer_product_service", return_value=service):
                response = app.test_client().get("/customers/1/drawings/files/9/download")
            # Windows 不允许删除仍被占用的文件，先读完数据并关闭响应再清理临时目录。
            response.get_data()
            response.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.data, b"\x89PNG\r\n\x1a\npayload")

    def test_preview_calls_service_with_preview_flag(self) -> None:
        app = self._route_app({"view_customers"})
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "drawing.pdf"
            path.write_bytes(b"%PDF-1.4\npayload\n%%EOF")
            payload = CustomerFilePayload(
                path=path,
                download_name="支架-v1.pdf",
                content_type="application/pdf",
                size_bytes=path.stat().st_size,
                sha256="",
                previewable=True,
            )

            def file_payload(*args, **kwargs):
                calls.append((args, kwargs))
                return payload

            service = SimpleNamespace(file_payload=file_payload)
            with patch.object(drawings_web, "get_customer_product_service", return_value=service):
                response = app.test_client().get("/customers/1/drawings/files/9/preview")
            # Windows 不允许删除仍被占用的文件，先读完数据并关闭响应再清理临时目录。
            response.get_data()
            response.close()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("attachment", response.headers["Content-Disposition"])
        args, kwargs = calls[0]
        self.assertEqual(args[0:2], (1, 9))
        self.assertEqual(kwargs["actor"], "tester")
        self.assertTrue(kwargs["for_preview"])

    def test_preview_validation_failure_redirects_with_flash(self) -> None:
        app = self._route_app({"view_customers"})

        def file_payload(*args, **kwargs):
            raise CustomerProductValidationError(
                "customer_drawing.preview_not_supported", "该文件格式不支持在线预览，请下载查看。"
            )

        service = SimpleNamespace(file_payload=file_payload)
        with patch.object(drawings_web, "get_customer_product_service", return_value=service):
            response = app.test_client().get("/customers/1/drawings/files/9/preview")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/customers/1", response.headers["Location"])

    def test_removed_mutation_routes_are_gone(self) -> None:
        app = self._route_app({"edit_customers", "delete_customers"})
        client = app.test_client()
        for rule in (
            "/customers/1/drawings",
            "/customers/1/drawings/2/update",
            "/customers/1/drawings/2/versions",
            "/customers/1/drawings/2/archive",
            "/customers/1/drawings/2/unarchive",
        ):
            self.assertEqual(client.post(rule).status_code, 404, rule)


if __name__ == "__main__":
    unittest.main()
