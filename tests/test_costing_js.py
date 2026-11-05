from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from tests.node_test_helper import run_node_test


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CostingJavaScriptTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable.")
    def test_costing_editor_state_helpers(self) -> None:
        run_node_test(self, PROJECT_ROOT / "tests/js/costing.test.mjs", project_root=PROJECT_ROOT)
