from __future__ import annotations

import shutil
import unittest
from pathlib import Path

try:
    from tests.node_test_helper import run_node_test
except ModuleNotFoundError:
    from node_test_helper import run_node_test


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = PROJECT_ROOT / "tests" / "js" / "customer_products.test.mjs"


class CustomerProductsJavaScriptTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable; customer products JavaScript was not run.")
    def test_customer_products_logic_with_node(self) -> None:
        run_node_test(self, NODE_TEST, project_root=PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
