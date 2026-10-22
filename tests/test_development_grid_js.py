from __future__ import annotations

import shutil
import unittest
from pathlib import Path

try:
    from tests.node_test_helper import run_node_test
except ModuleNotFoundError:
    from node_test_helper import run_node_test


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = PROJECT_ROOT / "tests" / "js" / "development_grid_state.test.mjs"


class DevelopmentGridJavaScriptTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable; development-grid state tests were not run.")
    def test_development_grid_state_with_node(self) -> None:
        run_node_test(self, NODE_TEST, project_root=PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
