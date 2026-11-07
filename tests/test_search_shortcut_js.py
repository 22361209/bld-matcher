from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from tests.node_test_helper import run_node_test


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SearchShortcutJavaScriptTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable; search shortcuts were not tested.")
    def test_search_focus_and_browser_fallback(self) -> None:
        run_node_test(self, PROJECT_ROOT / "tests/js/search_shortcut.test.mjs", project_root=PROJECT_ROOT)
