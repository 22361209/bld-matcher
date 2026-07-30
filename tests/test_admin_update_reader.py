from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.modules.admin.infrastructure import FileSystemUpdateReader


class AdminUpdateReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temporary_directory.name)
        fragments_dir = self.base_dir / "changes"
        fragments_dir.mkdir()
        (fragments_dir / "20260730-version.json").write_text(
            json.dumps(
                {
                    "date": "2026-07-30",
                    "version": "unreleased",
                    "title": "Version test",
                    "entries": ["Entry"],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _version(self, reader: FileSystemUpdateReader) -> str:
        return str(reader.read()[0]["version"])

    def test_environment_version_takes_priority(self) -> None:
        (self.base_dir / ".deployment-version").write_text("file123\n", encoding="utf-8")
        with (
            mock.patch.dict(os.environ, {"BLD_APP_VERSION": "env456"}),
            mock.patch("app.modules.admin.infrastructure.subprocess.run") as run,
        ):
            version = self._version(FileSystemUpdateReader(self.base_dir))

        self.assertEqual(version, "当前版本 env456")
        run.assert_not_called()

    def test_image_version_file_works_without_git(self) -> None:
        (self.base_dir / ".deployment-version").write_text("abc1234\n", encoding="utf-8")
        with (
            mock.patch.dict(os.environ, {"BLD_APP_VERSION": ""}),
            mock.patch(
                "app.modules.admin.infrastructure.subprocess.run",
                side_effect=FileNotFoundError,
            ) as run,
        ):
            version = self._version(FileSystemUpdateReader(self.base_dir))

        self.assertEqual(version, "当前版本 abc1234")
        run.assert_not_called()

    def test_git_head_is_the_local_development_fallback(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="deadbee\n", stderr="")
        with (
            mock.patch.dict(os.environ, {"BLD_APP_VERSION": ""}),
            mock.patch(
                "app.modules.admin.infrastructure.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            version = self._version(FileSystemUpdateReader(self.base_dir))

        self.assertEqual(version, "当前版本 deadbee")
        run.assert_called_once()

    def test_missing_non_repository_and_timeout_fall_back_to_pending(self) -> None:
        failures = (
            FileNotFoundError(),
            subprocess.CalledProcessError(128, ["git"]),
            subprocess.TimeoutExpired(["git"], 2),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), mock.patch.dict(
                os.environ,
                {"BLD_APP_VERSION": ""},
            ), mock.patch(
                "app.modules.admin.infrastructure.subprocess.run",
                side_effect=failure,
            ):
                version = self._version(FileSystemUpdateReader(self.base_dir))

            self.assertEqual(version, "待发布")


if __name__ == "__main__":
    unittest.main()
