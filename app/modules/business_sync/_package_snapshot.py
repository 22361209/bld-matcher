"""Bind previewed JSON, media and the confirmation token to the same package bytes."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ._package_archive import PackageReader


@contextmanager
def prepared_package(path: Path, reader: PackageReader):
    with tempfile.TemporaryDirectory(prefix="bld-business-sync-") as directory:
        snapshot = Path(directory) / path.name
        shutil.copyfile(path, snapshot)
        yield snapshot, reader(snapshot)
