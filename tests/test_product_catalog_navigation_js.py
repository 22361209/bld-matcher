from __future__ import annotations

import shutil
import unittest
from pathlib import Path

try:
    from tests.node_test_helper import run_node_test
except ModuleNotFoundError:
    from node_test_helper import run_node_test


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = PROJECT_ROOT / "tests" / "js" / "product_catalog_navigation.test.mjs"


class ProductCatalogNavigationJavaScriptTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable; product catalog navigation was not run.")
    def test_product_catalog_navigation_logic_with_node(self) -> None:
        run_node_test(self, NODE_TEST, project_root=PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
