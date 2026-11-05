from __future__ import annotations

import io
import gc
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from unittest.mock import patch


__all__ = (
    "PROJECT_ROOT",
    "Path",
    "SimpleNamespace",
    "WebAppTestBase",
    "io",
    "json",
    "patch",
    "pollute_xlsx_tail",
    "re",
    "shutil",
    "sqlite3",
    "strip_xlsx_dimension",
    "subprocess",
    "sys",
    "tarfile",
    "unittest",
    "unquote",
    "zipfile",
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_runtime_data_dirs = {(PROJECT_ROOT / "data").resolve()}


def _guard_runtime_sqlite():
    # Discovery may already have imported factories. Preserve the original destinations
    # across fixtures: removing a module cannot invalidate a previously imported function.
    loaded_config = sys.modules.get("app.config")
    for directory in (getattr(loaded_config, "DATA_DIR", None), os.environ.get("BLD_DATA_DIR")):
        if directory:
            _runtime_data_dirs.add(Path(directory).expanduser().resolve())
    connect = sqlite3.connect

    def guarded(database, *args, **kwargs):
        name = os.fsdecode(database)
        if name and name != ":memory:":
            filename = unquote(urlsplit(name).path) if name.startswith("file:") else name
            path = Path(filename).resolve()
            blocked = any(parent in _runtime_data_dirs for parent in path.parents)
            if not blocked and path.is_file() and path.stat().st_nlink > 1:
                databases = (directory / "products.sqlite3" for directory in _runtime_data_dirs)
                blocked = any(db.is_file() and path.samefile(db) for db in databases)
            if blocked:
                raise RuntimeError(
                    "WebAppTestBase blocked a runtime SQLite connection. Initialize the fixture first, "
                    "then import the application factory again in the isolated process."
                )
        return connect(database, *args, **kwargs)

    return patch.object(sqlite3, "connect", guarded)


def load_web_module():
    spec = spec_from_file_location("bld_matcher_test_web", PROJECT_ROOT / "app.py")
    module = module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["bld_matcher_test_web"] = module
    spec.loader.exec_module(module)
    return module


def pollute_xlsx_tail(path: Path, *, declared_rows: int = 2000, after_row: int = 251) -> None:
    temporary_path = path.with_suffix(".polluted.xlsx")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary_path, "w") as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                data = re.sub(
                    rb'<dimension ref="[^"]+"', f'<dimension ref="A1:A{declared_rows}"'.encode(), data, count=1
                )
                empty_rows = b"".join(
                    f'<row r="{row_index}" s="1" customFormat="1"/>'.encode()
                    for row_index in range(after_row, declared_rows + 1)
                )
                data = data.replace(b"</sheetData>", empty_rows + b"</sheetData>")
            target.writestr(entry, data)
    temporary_path.replace(path)


def strip_xlsx_dimension(path: Path) -> None:
    temporary_path = path.with_suffix(".no-dimension.xlsx")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary_path, "w") as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                data = re.sub(rb"<dimension\b[^>]*/>", b"", data, count=1)
            target.writestr(entry, data)
    temporary_path.replace(path)


class WebAppTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with ExitStack() as setup:
            setup.enter_context(_guard_runtime_sqlite())
            cls.tmp = tempfile.TemporaryDirectory()
            setup.callback(cls.tmp.cleanup)
            cls.root = Path(cls.tmp.name)
            cls._load_isolated_app()
            cls._resources = setup.pop_all()
            cls.addClassCleanup(cls._resources.close)

    @classmethod
    def _load_isolated_app(cls):
        root = cls.root
        os.environ["SECRET_KEY"] = "test-secret"
        os.environ["MAX_UPLOAD_MB"] = "20"
        os.environ["PRODUCT_SYNC_MAX_UPLOAD_MB"] = "512"
        os.environ["BLD_DATA_DIR"] = str(root / "data")
        os.environ["BLD_UPLOAD_DIR"] = str(root / "uploads")
        os.environ["BLD_OUTPUT_DIR"] = str(root / "outputs")
        os.environ["DEFAULT_ADMIN_PASSWORD"] = "test-admin-pw"
        os.environ["INTERNAL_API_TOKEN"] = ""
        for module_name in [name for name in sys.modules if name == "app" or name.startswith("app.")]:
            sys.modules.pop(module_name, None)
        cls.web = load_web_module()
        if not cls.web.DB_PATH.resolve().is_relative_to(root.resolve()):
            raise RuntimeError(f"Tests must use the isolated database under {root}, got {cls.web.DB_PATH}")
        cls.web.app.config["TESTING"] = True
        cls.client = cls.web.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.client = None
        cls.web = None
        gc.collect()
        cls._resources.close()

    def login(self):
        return self.client.post(
            "/login",
            data={"username": "007", "password": "test-admin-pw", "next": "/"},
            follow_redirects=False,
        )

    def register_customer_and_product(self, customer_name: str, bld_no: str):
        from app.modules.customers.service import customer_sync_id

        with self.web.connect(self.web.DB_PATH) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO customers (name, sync_id) VALUES (?, ?)",
                (customer_name, customer_sync_id(customer_name)),
            )
            conn.execute(
                "INSERT OR IGNORE INTO products (bld_no, created_at, updated_at) VALUES (?, datetime('now','localtime'), datetime('now','localtime'))",
                (bld_no,),
            )
            conn.commit()

    def cleanup_products(self, bld_pattern):
        with self.web.connect(self.web.DB_PATH) as conn:
            conn.execute("DELETE FROM products WHERE bld_no LIKE ?", (bld_pattern,))
            conn.commit()

    def cleanup_option_values(self, value_pattern):
        with self.web.connect(self.web.DB_PATH) as conn:
            conn.execute("DELETE FROM product_option_values WHERE value LIKE ?", (value_pattern,))
            conn.commit()

    def create_internal_api_token(self, *, scopes=None, name="OpenClaw Test"):
        from app.platform.api_keys import create_internal_api_key

        with self.web.connect(self.web.DB_PATH) as conn:
            return create_internal_api_key(conn, actor="tester", name=name, scopes=scopes)
