from __future__ import annotations

import ast
import unittest

from scripts import check_project_contract as contract


class ProjectContractTest(unittest.TestCase):
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

    def test_count_policy_rejects_growth_and_stale_baselines(self):
        errors = []
        contract._check_count_policy("debt", {"legacy.py": 2}, {"legacy.py": 3}, errors)
        contract._check_count_policy("debt", {"cleaned.py": 2}, {"cleaned.py": 1}, errors)
        self.assertEqual(len(errors), 2)
        self.assertIn("禁止增加", errors[0])
        self.assertIn("同步收紧白名单", errors[1])

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


if __name__ == "__main__":
    unittest.main()
