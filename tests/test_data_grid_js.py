from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = PROJECT_ROOT / "tests" / "js" / "data_grid.test.mjs"


class DataGridJavaScriptTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable; data-grid browser logic was not run.")
    def test_data_grid_width_logic_with_node(self) -> None:
        node = shutil.which("node")
        assert node is not None
        completed = subprocess.run(
            [node, "--test", str(NODE_TEST)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        self.assertEqual(completed.returncode, 0, output)


if __name__ == "__main__":
    unittest.main()
