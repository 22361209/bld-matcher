from __future__ import annotations

import shutil
import unittest
from pathlib import Path

try:
    from tests.node_test_helper import run_node_test
except ModuleNotFoundError:
    from node_test_helper import run_node_test


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = PROJECT_ROOT / "tests" / "js" / "inquiry_result.test.mjs"
INQUIRY_RESULT_ENTRY = PROJECT_ROOT / "static" / "pages" / "inquiry_result.js"
INQUIRY_RESULT_MODULES = tuple(
    sorted((PROJECT_ROOT / "static" / "pages").glob("inquiry_result_*.js"))
)


class InquiryResultJavaScriptTest(unittest.TestCase):
    def test_inquiry_result_modules_stay_within_split_size_targets(self) -> None:
        entry_source = INQUIRY_RESULT_ENTRY.read_text(encoding="utf-8")
        entry_lines = len(entry_source.splitlines())
        self.assertLessEqual(entry_lines, 200)
        self.assertEqual(entry_source.count("setupInquiryResultPage(document)"), 1)
        self.assertTrue(INQUIRY_RESULT_MODULES)
        for module in INQUIRY_RESULT_MODULES:
            with self.subTest(module=module.name):
                module_source = module.read_text(encoding="utf-8")
                line_count = len(module_source.splitlines())
                self.assertLessEqual(line_count, 400)
                self.assertNotIn("setupInquiryResultPage(document)", module_source)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node.js is unavailable; inquiry-result browser logic was not run.",
    )
    def test_inquiry_result_logic_with_node(self) -> None:
        run_node_test(self, NODE_TEST, project_root=PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
