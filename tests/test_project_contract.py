from __future__ import annotations

import ast
import unittest

from scripts import check_project_contract as contract


class ProjectContractTest(unittest.TestCase):
    def test_inline_script_rule_allows_external_module_only(self):
        self.assertIsNone(contract.INLINE_SCRIPT_RE.search('<script type="module" src="/static/page.js"></script>'))
        self.assertIsNotNone(contract.INLINE_SCRIPT_RE.search("<script>window.run()</script>"))
        self.assertIsNotNone(contract.INLINE_EVENT_RE.search('<button onclick="run()">Run</button>'))

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


if __name__ == "__main__":
    unittest.main()
