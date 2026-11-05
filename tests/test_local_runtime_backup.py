from __future__ import annotations

import hashlib
import errno
import json
import os
import socket
import sqlite3
import stat
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from tools import backup_local_runtime as backup


class LocalRuntimeBackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        for directory in backup.RUNTIME_DIRS:
            (self.project / directory).mkdir(parents=True)
        (self.project / "data/drawings").mkdir()
        (self.project / "data/drawings/part.pdf").write_bytes(b"drawing-fixture")
        (self.project / "uploads/input.xlsx").write_bytes(b"upload-fixture")
        (self.project / "outputs/result.xlsx").write_bytes(b"output-fixture")
        (self.project / ".env").write_text("SECRET_KEY=private-fixture-secret\n", encoding="utf-8")
        self.database = self.project / "data/products.sqlite3"
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE fixture (value TEXT)")
            connection.execute("INSERT INTO fixture VALUES ('baseline')")
        self.backup_root = self.root / "backups"
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for variable in ("BLD_DATA_DIR", "BLD_UPLOAD_DIR", "BLD_OUTPUT_DIR"):
            os.environ.pop(variable, None)

    def _create(self) -> Path:
        with patch.object(backup, "ensure_service_stopped"):
            return backup.create_backup(self.project, self.backup_root)

    def _trial_database(self) -> Path:
        path = self.project / "data/costing-trial/costing.sqlite3"
        path.parent.mkdir()
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE costs (code TEXT, amount TEXT)")
            connection.execute("INSERT INTO costs VALUES ('COST-BASE', '100')")
        return path

    def test_complete_private_backup_preserves_source_and_manifest_has_no_secret(self) -> None:
        before = backup._inventory(self.project)
        destination = self._create()
        self.assertEqual(backup._inventory(self.project), before)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((destination / ".env").stat().st_mode), 0o600)
        self.assertFalse((destination / "INCOMPLETE.json").exists())
        manifest_text = (destination / "manifest.json").read_text()
        self.assertNotIn("private-fixture-secret", manifest_text)
        self.assertNotIn("SECRET_KEY", manifest_text)
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["status"], "complete")
        for entry in manifest["files"]:
            path = destination / entry["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])
        self.assertTrue((destination / "data/drawings/part.pdf").is_file())
        self.assertTrue((destination / "uploads/input.xlsx").is_file())
        self.assertTrue((destination / "outputs/result.xlsx").is_file())

    def test_snapshot_includes_committed_wal_without_changing_source_data(self) -> None:
        writer = sqlite3.connect(self.database)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO fixture VALUES ('committed-in-wal')")
        writer.commit()
        wal = Path(str(self.database) + "-wal")
        self.assertGreater(wal.stat().st_size, 0)
        before_database, before_wal = self.database.read_bytes(), wal.read_bytes()
        destination = self._create()
        self.assertEqual(self.database.read_bytes(), before_database)
        self.assertEqual(wal.read_bytes(), before_wal)
        with sqlite3.connect(destination / "data/products.sqlite3") as snapshot:
            self.assertEqual(snapshot.execute("SELECT value FROM fixture").fetchall(), [("baseline",), ("committed-in-wal",)])
        self.assertFalse((destination / "data/products.sqlite3-wal").exists())
        self.assertFalse((destination / "data/products.sqlite3-shm").exists())

    def test_optional_trial_cost_database_uses_backup_api_and_includes_committed_wal(self) -> None:
        trial = self._trial_database()
        (trial.parent / "access.json").write_text('{"fixture":true}', encoding="utf-8")
        writer = sqlite3.connect(trial)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO costs VALUES ('COST-WAL', '140')")
        writer.commit()
        wal = Path(str(trial) + "-wal")
        self.assertGreater(wal.stat().st_size, 0)
        before_database, before_wal = trial.read_bytes(), wal.read_bytes()
        destination = self._create()
        snapshot = destination / "data/costing-trial/costing.sqlite3"
        self.assertEqual(trial.read_bytes(), before_database)
        self.assertEqual(wal.read_bytes(), before_wal)
        with sqlite3.connect(snapshot) as connection:
            self.assertEqual(connection.execute("SELECT * FROM costs ORDER BY code").fetchall(),
                             [("COST-BASE", "100"), ("COST-WAL", "140")])
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(snapshot) + suffix).exists())
        methods = {row["path"]: row["method"] for row in json.loads((destination / "manifest.json").read_text())["files"]}
        self.assertEqual(methods["data/products.sqlite3"], "sqlite-backup-api")
        self.assertEqual(methods["data/costing-trial/costing.sqlite3"], "sqlite-backup-api")
        self.assertEqual(methods["data/costing-trial/access.json"], "file-copy")

    def test_optional_trial_sidecars_are_validated_instead_of_copied(self) -> None:
        trial = self._trial_database()
        original = trial.read_bytes()
        for suffix, error in (("-journal", "unsafe-journal-state"), ("-wal", "unsafe-wal-state")):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(trial) + suffix)
                sidecar.write_bytes(b"unclean-fixture")
                try:
                    with self.assertRaises(backup.BackupError) as caught:
                        self._create()
                    self.assertEqual(caught.exception.code, error)
                    self.assertEqual(trial.read_bytes(), original)
                    self.assertEqual(sidecar.read_bytes(), b"unclean-fixture")
                    self.assertFalse((caught.exception.backup_dir / "manifest.json").exists())
                finally:
                    sidecar.unlink()

    def test_trial_orphan_sidecars_are_not_silently_omitted(self) -> None:
        directory = self.project / "data/costing-trial"
        directory.mkdir()
        (directory / "costing.sqlite3-wal").write_bytes(b"orphan-wal")
        with self.assertRaises(backup.BackupError) as caught:
            self._create()
        self.assertEqual(caught.exception.code, "invalid-layout")

    def test_trial_shared_memory_changes_do_not_change_business_inventory(self) -> None:
        trial = self._trial_database()
        shm = Path(str(trial) + "-shm")
        before = backup._inventory(self.project)
        shm.write_bytes(b"read-lock-state")
        self.assertEqual(backup._inventory(self.project), before)
        shm.write_bytes(b"new-read-lock-state")
        self.assertEqual(backup._inventory(self.project), before)
        shm.unlink()
        self.assertEqual(backup._inventory(self.project), before)

    def test_recursive_target_is_rejected_without_creating_any_backup(self) -> None:
        with self.assertRaises(backup.BackupError) as caught:
            backup.create_backup(self.project, self.project / "data/backups")
        self.assertEqual(caught.exception.code, "recursive-target")
        self.assertFalse((self.project / "data/backups").exists())

    def test_active_listener_is_rejected_and_incomplete_evidence_is_retained(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            with self.assertRaises(backup.BackupError) as caught:
                backup.create_backup(self.project, self.backup_root, port=port)
        self.assertEqual(caught.exception.code, "active-service")
        destination = caught.exception.backup_dir
        self.assertIsNotNone(destination)
        evidence = json.loads((destination / "INCOMPLETE.json").read_text())
        self.assertEqual(evidence["stage"], "preflight")
        self.assertEqual(evidence["error_code"], "active-service")
        self.assertFalse((destination / "manifest.json").exists())

    def test_custom_paths_and_symlinks_are_rejected_without_reading_the_target(self) -> None:
        os.environ["BLD_DATA_DIR"] = str(self.root / "other-data")
        with self.assertRaises(backup.BackupError) as caught:
            self._create()
        self.assertEqual(caught.exception.code, "custom-runtime-path")
        os.environ.pop("BLD_DATA_DIR")
        (self.project / "uploads/external").symlink_to(self.root / "outside-secret")
        with self.assertRaises(backup.BackupError) as caught:
            self._create()
        self.assertEqual(caught.exception.code, "unsafe-source-file")

    def test_source_changes_invalidate_whole_backup(self) -> None:
        copy = backup._copy_file

        def changing_copy(source: Path, destination: Path, expected: backup.FileState) -> None:
            copy(source, destination, expected)
            if source.name == "input.xlsx":
                source.write_bytes(b"changed-while-copying")

        with patch.object(backup, "_copy_file", side_effect=changing_copy):
            with self.assertRaises(backup.BackupError) as caught:
                self._create()
        self.assertEqual(caught.exception.code, "source-changed")
        destination = caught.exception.backup_dir
        self.assertTrue((destination / "INCOMPLETE.json").is_file())
        self.assertFalse((destination / "manifest.json").exists())

    def test_unreadable_directory_cannot_silently_produce_complete_backup(self) -> None:
        def unreadable_walk(_top, *, followlinks, onerror):
            onerror(PermissionError("private-fixture-must-not-be-logged"))
            return iter(())

        with patch.object(backup.os, "walk", side_effect=unreadable_walk):
            with self.assertRaises(backup.BackupError) as caught:
                self._create()
        destination = caught.exception.backup_dir
        evidence = (destination / "INCOMPLETE.json").read_text()
        self.assertEqual(json.loads(evidence)["stage"], "source-inventory")
        self.assertNotIn("private-fixture-must-not-be-logged", evidence)
        self.assertFalse((destination / "manifest.json").exists())

    def test_leftover_rollback_journal_is_rejected_without_source_recovery(self) -> None:
        journal = Path(str(self.database) + "-journal")
        journal.write_bytes(b"leftover-journal-fixture")
        original_database = self.database.read_bytes()
        with self.assertRaises(backup.BackupError) as caught:
            self._create()
        self.assertEqual(caught.exception.code, "unsafe-journal-state")
        self.assertEqual(journal.read_bytes(), b"leftover-journal-fixture")
        self.assertEqual(self.database.read_bytes(), original_database)

    def test_outbound_nas_connection_does_not_block_stopped_local_service_backup(self) -> None:
        def lsof_with_only_outbound_connection(command, **_kwargs):
            if "-sTCP:LISTEN" in command:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout="browser 123 user TCP 192.168.110.10:52000->192.168.110.93:5055 (ESTABLISHED)\n",
                stderr="",
            )

        with patch.object(backup.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.connect.side_effect = ConnectionRefusedError(
                errno.ECONNREFUSED, "fixture: no local listener",
            )
            with patch.object(backup.shutil, "which", return_value="/usr/sbin/lsof"):
                with patch.object(backup.subprocess, "run", side_effect=lsof_with_only_outbound_connection) as run:
                    destination = backup.create_backup(self.project, self.backup_root)
        self.assertEqual(json.loads((destination / "manifest.json").read_text())["status"], "complete")
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all("-sTCP:LISTEN" in call.args[0] for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
