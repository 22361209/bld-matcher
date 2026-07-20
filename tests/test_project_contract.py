from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import unittest

from scripts import check_project_contract as contract


class ProjectContractTest(unittest.TestCase):
    def test_all_complete_pages_share_shell_and_have_unique_protocol_ids(self):
        policy = json.loads((contract.ROOT / "policy" / "legacy_allowlist.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["legacy_full_page_templates"], [])
        self.assertEqual(policy["legacy_inline_script_counts"], {})
        self.assertEqual(policy["legacy_inline_style_counts"], {})

        page_ids = []
        for path in sorted((contract.ROOT / "templates").glob("*.html")):
            if path.name == "base.html" or path.name.startswith("_"):
                continue
            template = path.read_text(encoding="utf-8")
            self.assertRegex(template, r'{%\s*extends\s+"base\.html"\s*%}', path.name)
            self.assertIsNone(contract.INLINE_SCRIPT_RE.search(template), path.name)
            self.assertIsNone(contract.INLINE_EVENT_RE.search(template), path.name)
            self.assertIsNone(contract.INLINE_STYLE_RE.search(template), path.name)
            page_id = re.search(r'{%\s*block\s+page_id\s*%}\s*([a-z0-9_.-]+)', template)
            self.assertIsNotNone(page_id, path.name)
            page_ids.append(page_id.group(1))
        self.assertEqual(len(page_ids), len(set(page_ids)))

    def test_inline_script_rule_allows_external_module_only(self):
        self.assertIsNone(contract.INLINE_SCRIPT_RE.search('<script type="module" src="/static/page.js"></script>'))
        self.assertIsNotNone(contract.INLINE_SCRIPT_RE.search("<script>window.run()</script>"))
        self.assertIsNotNone(contract.INLINE_EVENT_RE.search('<button onclick="run()">Run</button>'))

    def test_machine_api_path_matching_is_exact(self):
        from app.platform.request_context import is_machine_api_path

        self.assertTrue(is_machine_api_path("/api/v1"))
        self.assertTrue(is_machine_api_path("/api/quotes/42"))
        self.assertFalse(is_machine_api_path("/api/v10"))
        self.assertFalse(is_machine_api_path("/api/quotes-preview"))

    def test_route_declaration_reads_route_methods_and_keyword_rule(self):
        tree = ast.parse(
            """
@app.route(rule="/api/v1/jobs", methods=["POST", "DELETE"])
def jobs():
    pass
"""
        )
        function = tree.body[0]
        self.assertIsInstance(function, ast.FunctionDef)
        self.assertEqual(
            contract._route_declaration(function.decorator_list[0]),
            ("/api/v1/jobs", {"POST", "DELETE"}),
        )

    def test_dynamic_route_path_cannot_evade_contract_scan(self):
        tree = ast.parse(
            """
@app.post(route_name)
def dynamic_route():
    pass
"""
        )
        function = tree.body[0]
        self.assertIsInstance(function, ast.FunctionDef)
        decorator = function.decorator_list[0]
        self.assertTrue(contract._is_route_decorator(decorator))
        self.assertIsNone(contract._route_declaration(decorator))

    def test_route_adapter_limits_reject_concentration_and_dynamic_registration(self):
        oversized = "\n".join(
            ["# padding"] * contract.ROUTE_ADAPTER_MAX_LINES
            + ["@app.get('/oversized')", "def oversized_route():", "    pass"]
        )
        size_errors = []
        contract._check_route_adapter_limits(
            "app/modules/example/oversized_web.py",
            oversized,
            ast.parse(oversized),
            size_errors,
        )
        self.assertTrue(any("超过 320 行" in error for error in size_errors))

        crowded = "\n\n".join(
            f"@app.get('/route-{index}')\ndef route_{index}():\n    pass"
            for index in range(contract.ROUTE_ADAPTER_MAX_ENDPOINTS + 1)
        )
        endpoint_errors = []
        contract._check_route_adapter_limits(
            "app/modules/example/crowded_web.py",
            crowded,
            ast.parse(crowded),
            endpoint_errors,
        )
        self.assertTrue(any("超过 15 个 endpoint" in error for error in endpoint_errors))

        dynamic = "app.add_url_rule('/hidden', view_func=handler)"
        dynamic_errors = []
        contract._check_route_adapter_limits(
            "app/modules/example/dynamic_web.py",
            dynamic,
            ast.parse(dynamic),
            dynamic_errors,
        )
        self.assertTrue(any("禁止 add_url_rule" in error for error in dynamic_errors))

    def test_decomposed_processing_modules_have_hard_size_limits(self):
        errors = []
        contract._check_decomposed_processing_sizes(errors)
        self.assertEqual(errors, [])

        oversized_errors = []
        contract._check_python_source_size(
            "app/modules/example/oversized.py",
            "\n".join(["# padding"] * (contract.PROCESSING_MODULE_MAX_LINES + 1)),
            contract.PROCESSING_MODULE_MAX_LINES,
            oversized_errors,
            label="处理模块",
        )
        self.assertTrue(any("必须继续按职责拆分" in error for error in oversized_errors))

    def test_count_policy_rejects_growth_and_stale_baselines(self):
        errors = []
        contract._check_count_policy("debt", {"legacy.py": 2}, {"legacy.py": 3}, errors)
        contract._check_count_policy("debt", {"cleaned.py": 2}, {"cleaned.py": 1}, errors)
        self.assertEqual(len(errors), 2)
        self.assertIn("禁止增加", errors[0])
        self.assertIn("同步收紧白名单", errors[1])

    def test_file_line_policy_tracks_exact_current_size(self):
        policy = json.loads((contract.ROOT / "policy" / "legacy_allowlist.json").read_text(encoding="utf-8"))
        expected = policy["legacy_file_line_counts"]
        actual = {
            relative: len((contract.ROOT / relative).read_text(encoding="utf-8").splitlines())
            for relative in expected
        }
        self.assertEqual(actual, expected)

    def test_css_parser_and_layer_rules_reject_specificity_and_domain_drift(self):
        source = """
/* ignored .product-comment {} */
@media (max-width: 760px) {
  .product-card, #result-panel { color: red !important; }
}
"""
        self.assertEqual(
            contract._css_selectors(source),
            [".product-card", "#result-panel"],
        )
        errors = []
        contract._check_css_source(
            "static/components/example.css",
            source,
            contract.CSS_COMPONENT_MAX_LINES,
            errors,
            shared=True,
        )
        self.assertTrue(any("ID 选择器" in error for error in errors))
        self.assertTrue(any("!important" in error for error in errors))
        self.assertTrue(any("业务选择器" in error for error in errors))

    def test_current_css_assets_follow_layer_and_ownership_contract(self):
        errors = []
        contract._check_css_governance(errors)
        self.assertEqual(errors, [])

    def test_page_behavior_stays_out_of_global_script(self):
        global_script = (contract.ROOT / "static" / "app.js").read_text(encoding="utf-8")
        page_only_markers = (
            "data-quick-results",
            "data-history-loader",
            "data-quick-oe-image",
            "shipment-folder-picker",
            "data-price-mode",
            "data-open-download-modal",
            "data-purchase-contract-form",
        )
        for marker in page_only_markers:
            self.assertNotIn(marker, global_script)

    def test_primary_data_lists_share_resizable_grid_protocol(self):
        product_shell = (contract.ROOT / "templates" / "products.html").read_text(encoding="utf-8")
        product_results = (contract.ROOT / "templates" / "_products_results.html").read_text(encoding="utf-8")
        for template_name in ("products.html", "materials.html", "tubes.html", "quotes.html"):
            template = (contract.ROOT / "templates" / template_name).read_text(encoding="utf-8")
            if template_name == "products.html":
                template += product_results
            self.assertIn("data-resizable-grid", template, template_name)
            self.assertIn("data-grid-scroll", template, template_name)
            self.assertIn('include "_data_grid_footer.html"', template, template_name)

        products_template = product_shell + product_results
        self.assertNotIn("resizable-table", products_template)
        self.assertIn("data-grid-heading-overflow", products_template)
        for template_name in ("products.html", "materials.html", "tubes.html", "quotes.html"):
            template = (contract.ROOT / "templates" / template_name).read_text(encoding="utf-8")
            if template_name == "products.html":
                template += product_results
            self.assertIn("data_grid_footer_context", template, template_name)

        self.assertNotIn('~ " · 当前 "', products_template)
        materials_template = (contract.ROOT / "templates" / "materials.html").read_text(encoding="utf-8")
        self.assertIn('"总明细 "', materials_template)
        self.assertIn('" · 启用 "', materials_template)
        self.assertIn('" · 停用 "', materials_template)
        tubes_template = (contract.ROOT / "templates" / "tubes.html").read_text(encoding="utf-8")
        self.assertIn('"总管件 "', tubes_template)
        self.assertIn("data-grid-heading-overflow", tubes_template)

        base = (contract.ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("components/data_grid.css", base)
        self.assertIn("components/data_grid.js", base)

    def test_split_page_assets_are_declared_by_owning_templates(self):
        asset_owners = {
            "index.html": (
                "pages/inquiry.css",
                "pages/home.css",
                "pages/history.css",
                "pages/home.js",
            ),
            "internal_api_key.html": ("pages/api_keys.css",),
            "login.html": ("pages/login.css",),
            "material_drawings.html": ("pages/material_drawings.css", "pages/material_drawings.js"),
            "materials.html": ("pages/materials.css", "pages/history.css"),
            "product_data_sync.html": ("pages/product_sync.css",),
            "product_form.html": ("pages/products.css",),
            "products.html": ("pages/products.css", "pages/products.js"),
            "purchase_contracts.html": ("pages/contracts.css", "pages/contracts.js"),
            "quotes.html": ("pages/quotes.css",),
            "result.html": ("pages/inquiry.css", "pages/inquiry_result.js"),
            "select_match_column.html": ("pages/inquiry.css",),
            "system_updates.html": ("pages/system_updates.css",),
        }
        for template_name, assets in asset_owners.items():
            template = (contract.ROOT / "templates" / template_name).read_text(encoding="utf-8")
            for asset in assets:
                self.assertIn(asset, template, f"{template_name} must load {asset}")

        base = (contract.ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        for path in sorted((contract.ROOT / "static" / "components").glob("*.css")):
            self.assertIn(f"components/{path.name}", base)

    def test_route_snapshot_matches_runtime_contract(self):
        result = subprocess.run(
            [sys.executable, "scripts/route_snapshot.py", "--check"],
            cwd=contract.ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_database_boundary_allows_connection_infrastructure_only(self):
        clean = ast.parse("def connect(path):\n    return path\n")
        dirty = ast.parse("def connect(path):\n    return path\n\ndef save_product():\n    pass\n")
        self.assertEqual(contract._database_boundary_errors(clean), [])
        self.assertIn("save_product", contract._database_boundary_errors(dirty)[0])

    def test_legacy_api_path_counts_allow_adapter_move_without_endpoint_growth(self):
        before = [
            "app/routes/quotes.py:create /api/quotes",
            "app/routes/quotes.py:list /api/quotes",
        ]
        after = [
            "app/modules/quotes/api.py:create /api/quotes",
            "app/modules/quotes/api.py:list /api/quotes",
        ]
        self.assertEqual(
            contract._legacy_api_path_counts(before),
            contract._legacy_api_path_counts(after),
        )

    def test_openapi_declaration_reads_path_method_and_scopes(self):
        tree = ast.parse(
            """
register_openapi_operation(
    OpenApiOperation(
        path="/api/v1/quotes/{quote_id}",
        method="PATCH",
        operation_id="updateQuote",
        summary="Update quote",
        scopes=("quotes:write",),
    )
)
"""
        )
        self.assertEqual(
            contract._openapi_declarations(tree),
            {("/api/v1/quotes/{quote_id}", "PATCH"): {"quotes:write"}},
        )
        self.assertEqual(contract._openapi_request_model_operations(tree), set())

    def test_route_contract_requires_v1_idempotency_and_static_scope(self):
        tree = ast.parse(
            """
@app.post("/api/v1/quotes")
@api_scope_required("quotes:write")
def create_quote():
    pass
"""
        )
        errors = []
        _has_routes, _legacy, operations = contract._check_route_contracts(
            contract.ROOT / "app" / "api" / "v1" / "quotes.py",
            tree,
            errors,
            set(),
            set(),
        )
        self.assertEqual(operations, {("/api/v1/quotes", "POST"): {"quotes:write"}})
        self.assertTrue(any("幂等保护" in error for error in errors))
        self.assertTrue(any("Pydantic Schema" in error for error in errors))

        patch_tree = ast.parse(
            """
@app.patch("/api/v1/quotes/<int:quote_id>")
@api_scope_required("quotes:write")
@idempotency_required
@api_schema(QuotePatchRequest)
def update_quote(quote_id):
    pass
"""
        )
        patch_errors = []
        contract._check_route_contracts(
            contract.ROOT / "app" / "api" / "v1" / "quotes.py",
            patch_tree,
            patch_errors,
            set(),
            set(),
        )
        self.assertTrue(any("If-Match" in error for error in patch_errors))

    def test_module_layer_rule_rejects_domain_and_adapter_infrastructure_imports(self):
        domain_errors = []
        contract._check_module_layer(
            "app/modules/quotes/domain.py",
            ast.parse("from flask import request\nimport sqlite3\n"),
            domain_errors,
        )
        self.assertEqual(len(domain_errors), 1)
        self.assertIn("flask", domain_errors[0])
        self.assertIn("sqlite3", domain_errors[0])

        api_errors = []
        contract._check_module_layer(
            "app/modules/quotes/api.py",
            ast.parse("from app.database import connect\n"),
            api_errors,
        )
        self.assertEqual(len(api_errors), 1)

        suffixed_service_errors = []
        contract._check_module_layer(
            "app/modules/products/sync_service.py",
            ast.parse("import sqlite3\n"),
            suffixed_service_errors,
        )
        self.assertEqual(len(suffixed_service_errors), 1)

        suffixed_web_errors = []
        contract._check_module_layer(
            "app/modules/admin/auth_web.py",
            ast.parse("from app.database import connect\n"),
            suffixed_web_errors,
        )
        self.assertEqual(len(suffixed_web_errors), 1)


if __name__ == "__main__":
    unittest.main()
