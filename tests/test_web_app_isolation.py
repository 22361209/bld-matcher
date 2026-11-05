import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


def exercise_early_factory():
    from app.modules.materials.factory import get_material_service
    from tests import web_app_test_base as fixture

    checks = unittest.TestCase()
    runtime = Path(os.environ["BLD_DATA_DIR"])
    other = runtime.parent / "independent"
    other.mkdir()
    runtime.mkdir()
    original = runtime / "products.sqlite3"
    original.write_bytes(b"untouched runtime fixture")
    link = other / "linked.sqlite3"
    os.link(original, link)
    symlink = other / "symlink.sqlite3"
    symlink.symlink_to(original)
    original_connect = sqlite3.connect
    checks.assertIn((fixture.PROJECT_ROOT / "data").resolve(), fixture._runtime_data_dirs)
    # Model the default directory too, so even the hardlink identity check uses only temp files.
    with patch.object(fixture, "_runtime_data_dirs", {runtime.parent / "default-data"}):
        for _ in range(2):
            fixture.WebAppTestBase.setUpClass()
            try:
                with checks.assertRaisesRegex(RuntimeError, "Initialize the fixture first"):
                    get_material_service().save_item({"model": "TEST", "code": "A", "part": "主板"}, actor="test")
                for path in (link, symlink, original.as_uri() + "?mode=rw"):
                    with checks.assertRaisesRegex(RuntimeError, "blocked a runtime SQLite"):
                        sqlite3.connect(path, uri=True)
                for allowed in (fixture.WebAppTestBase.web.DB_PATH, other / "separate.db", ":memory:"):
                    with closing(sqlite3.connect(allowed)) as db:
                        checks.assertEqual(db.execute("SELECT 1").fetchone(), (1,))
            finally:
                fixture.WebAppTestBase.tearDownClass()
            checks.assertIs(sqlite3.connect, original_connect)
        checks.assertEqual(original.read_bytes(), b"untouched runtime fixture")


class TestWebAppIsolation(unittest.TestCase):
    def test_early_bound_material_factory_cannot_connect_to_original_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = dict(os.environ, BLD_DATA_DIR=str(root / "original data"), BLD_UPLOAD_DIR=str(root / "uploads"),
                       BLD_OUTPUT_DIR=str(root / "outputs"), SECRET_KEY="test-secret")
            result = subprocess.run(
                [sys.executable, "-c", "from tests.test_web_app_isolation import exercise_early_factory; exercise_early_factory()"],
                env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
