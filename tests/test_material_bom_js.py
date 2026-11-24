from pathlib import Path
import unittest

from tests.node_test_helper import run_node_test


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MaterialBOMJSTests(unittest.TestCase):
    def test_tree_visibility(self):
        run_node_test(self, PROJECT_ROOT / "tests/js/material_bom.test.mjs", project_root=PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
