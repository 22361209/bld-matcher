from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


def run_node_test(
    test_case: unittest.TestCase,
    node_test: Path,
    *,
    project_root: Path,
) -> None:
    node = shutil.which("node")
    assert node is not None
    completed = subprocess.run(
        [node, "--test", str(node_test)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    test_case.assertEqual(completed.returncode, 0, output)
