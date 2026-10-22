"""Freeze v5 development bytes so every validation and write sees one package."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ._database_apply import apply_package as apply_database_package


def apply_package(database_path: Path, package_path: Path, **kwargs):
    manifest, payload = kwargs["read_package_fn"](package_path)
    if "development" not in payload:
        kwargs["read_package_fn"] = lambda _path: (manifest, payload)
        return apply_database_package(database_path, package_path, **kwargs)
    with tempfile.TemporaryDirectory(prefix="bld-development-sync-") as directory:
        snapshot = Path(directory) / package_path.name
        shutil.copyfile(package_path, snapshot)
        return apply_database_package(database_path, snapshot, original_package_path=package_path, **kwargs)
