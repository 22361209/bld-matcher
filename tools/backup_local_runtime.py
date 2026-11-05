"""Create a private, verified offline runtime backup without initializing the app."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_ROOT = Path.home() / "Library/Application Support/BLD/backups"
RUNTIME_DIRS = ("data", "uploads", "outputs")
DATABASE_PATHS = ("data/products.sqlite3", "data/costing-trial/costing.sqlite3")
DATABASE_FILES = {f"{database}{suffix}" for database in DATABASE_PATHS for suffix in ("", "-wal", "-shm", "-journal")}
DATABASE_SHM_FILES = {f"{database}-shm" for database in DATABASE_PATHS}
ERROR_MESSAGES = {
    "recursive-target": "The backup destination must be outside the project directory.",
    "invalid-layout": "Expected regular runtime directories, .env, and products.sqlite3 were not found.",
    "custom-runtime-path": "A BLD runtime directory override does not match this project's default layout.",
    "unsafe-source-file": "Runtime symlinks and special files are not supported; no external targets are copied.",
    "active-service": "Local TCP port 5055 is active; stop the service before creating an offline backup.",
    "service-check-failed": "Could not establish that the local service is stopped.",
    "unsafe-wal-state": "A WAL exists without its shared-memory file; cleanly stop its owning process first.",
    "unsafe-journal-state": "A SQLite rollback journal remains; recover and cleanly stop its owning process first.",
    "backup-timeout": "The SQLite snapshot could not finish within 30 seconds.",
    "snapshot-integrity": "The SQLite backup failed its integrity check.",
    "source-changed": "Runtime files changed during the backup; the backup remains incomplete.",
    "copy-mismatch": "A copied file did not match its source fingerprint.",
    "backup-failed": "The backup did not complete; inspect the retained incomplete evidence.",
}


class BackupError(RuntimeError):
    def __init__(self, code: str, backup_dir: Path | None = None) -> None:
        self.code = code
        self.backup_dir = backup_dir
        super().__init__(ERROR_MESSAGES[code])


@dataclass(frozen=True)
class FileState:
    size: int
    mtime_ns: int
    mode: int
    sha256: str


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise BackupError("unsafe-source-file")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def ensure_service_stopped(port: int = 5055) -> None:
    """Check loopback reachability and local TCP listeners where lsof is available."""
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                probe.connect((address, port))
        except OSError as error:
            if error.errno not in {errno.ECONNREFUSED, errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL}:
                raise BackupError("service-check-failed") from None
        else:
            raise BackupError("active-service")
    executable = shutil.which("lsof")
    if executable is None and sys.platform == "darwin":
        executable = "/usr/sbin/lsof"
    if executable:
        try:
            result = subprocess.run(
                [executable, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise BackupError("service-check-failed") from None
        if result.returncode == 0:
            raise BackupError("active-service")
        if result.returncode != 1 or result.stderr.strip():
            raise BackupError("service-check-failed")


def _validate_layout(project: Path) -> None:
    paths = [project / name for name in RUNTIME_DIRS]
    paths.extend((project / ".env", project / "data/products.sqlite3"))
    if any(path.is_symlink() for path in paths):
        raise BackupError("unsafe-source-file")
    if not all((project / name).is_dir() for name in RUNTIME_DIRS):
        raise BackupError("invalid-layout")
    if not (project / ".env").is_file() or not (project / "data/products.sqlite3").is_file():
        raise BackupError("invalid-layout")
    for relative in DATABASE_PATHS[1:]:
        database = project / relative
        if database.is_symlink():
            raise BackupError("unsafe-source-file")
        present = database.exists() or any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))
        if present and not database.is_file():
            # Do not silently discard orphaned sidecars or copy an unexpected directory.
            raise BackupError("invalid-layout")
    file_values: dict[str, str] = {}
    for raw in (project / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            file_values[key.strip()] = value.strip().strip('"').strip("'")
    for variable, directory in zip(("BLD_DATA_DIR", "BLD_UPLOAD_DIR", "BLD_OUTPUT_DIR"), RUNTIME_DIRS):
        for value in (os.environ.get(variable, ""), file_values.get(variable, "")):
            if value:
                candidate = Path(value).expanduser()
                if not candidate.is_absolute():
                    candidate = project / candidate
                if candidate.resolve() != project / directory:
                    raise BackupError("custom-runtime-path")


def _inventory(project: Path) -> tuple[list[str], dict[str, FileState]]:
    def fail_walk(error: OSError) -> None:
        raise error

    directories: list[str] = []
    files = [project / ".env"]
    for name in RUNTIME_DIRS:
        for current, dirnames, filenames in os.walk(project / name, followlinks=False, onerror=fail_walk):
            current_path = Path(current)
            directories.append(current_path.relative_to(project).as_posix())
            for dirname in dirnames:
                if (current_path / dirname).is_symlink():
                    raise BackupError("unsafe-source-file")
            files.extend(current_path / filename for filename in filenames)
    result: dict[str, FileState] = {}
    for path in sorted(files):
        relative = path.relative_to(project).as_posix()
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if relative in DATABASE_SHM_FILES:
                continue
            raise
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupError("unsafe-source-file")
        if relative in DATABASE_SHM_FILES:
            continue  # Ephemeral SQLite read locks are not runtime business data.
        result[relative] = FileState(metadata.st_size, metadata.st_mtime_ns, stat.S_IMODE(metadata.st_mode), _sha256(path))
    return sorted(directories), result


def _snapshot_database(source_path: Path, target_path: Path) -> None:
    wal_path = source_path.with_name(source_path.name + "-wal")
    shm_path = source_path.with_name(source_path.name + "-shm")
    if source_path.with_name(source_path.name + "-journal").exists():
        raise BackupError("unsafe-journal-state")
    if wal_path.exists() and not shm_path.is_file():
        raise BackupError("unsafe-wal-state")
    # An immutable read avoids creating SQLite sidecars when there is no WAL.
    # Existing WALs must be read normally so committed WAL data is included.
    uri = source_path.as_uri() + "?mode=ro" + ("" if wal_path.exists() else "&immutable=1")
    started = time.monotonic()

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() - started > 30:
            raise BackupError("backup-timeout")

    source = sqlite3.connect(uri, uri=True, timeout=3)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target, pages=256, progress=progress, sleep=0.1)
        target.commit()
        target.execute("PRAGMA journal_mode=DELETE")
        if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("snapshot-integrity")
    finally:
        target.close()
        source.close()
    target_path.chmod(0o600)


def _copy_file(source: Path, destination: Path, expected: FileState) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, flags)
    with os.fdopen(source_fd, "rb") as source_handle:
        if not stat.S_ISREG(os.fstat(source_handle.fileno()).st_mode):
            raise BackupError("unsafe-source-file")
        target_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(target_fd, "wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
    if destination.stat().st_size != expected.size or _sha256(destination) != expected.sha256:
        raise BackupError("copy-mismatch")


def create_backup(project_dir: Path, backup_root: Path, *, port: int = 5055) -> Path:
    project = project_dir.expanduser().resolve()
    parent = backup_root.expanduser().resolve()
    if parent == project or parent.is_relative_to(project):
        raise BackupError("recursive-target")
    started = _now()
    destination = parent / f"docker-desktop-{started.strftime('%Y%m%dT%H%M%S%z')}-{uuid4().hex[:8]}"
    parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    marker = destination / "INCOMPLETE.json"
    stage = "preflight"
    evidence: dict[str, object] = {"status": "incomplete", "started_at": started.isoformat(), "stage": stage}
    _write_json(marker, evidence)
    try:
        _validate_layout(project)
        ensure_service_stopped(port)
        stage = "source-inventory"
        _write_json(marker, {**evidence, "stage": stage})
        directories, before = _inventory(project)
        for directory in directories:
            (destination / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
        stage = "sqlite-snapshot"
        _write_json(marker, {**evidence, "stage": stage})
        for relative in DATABASE_PATHS:
            if relative in before:
                _snapshot_database(project / relative, destination / relative)
        stage = "copy-files"
        _write_json(marker, {**evidence, "stage": stage})
        for relative, fingerprint in before.items():
            if relative not in DATABASE_FILES:
                _copy_file(project / relative, destination / relative, fingerprint)
        stage = "verification"
        _write_json(marker, {**evidence, "stage": stage})
        ensure_service_stopped(port)
        if _inventory(project) != (directories, before):
            raise BackupError("source-changed")
        backup_files: list[dict[str, object]] = []
        for path in sorted(destination.rglob("*")):
            if path.is_file() and path != marker:
                backup_files.append({
                    "path": path.relative_to(destination).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                    "method": "sqlite-backup-api" if path.relative_to(destination).as_posix() in DATABASE_PATHS else "file-copy",
                })
        _write_json(destination / "manifest.json", {
            "status": "complete", "started_at": started.isoformat(), "finished_at": _now().isoformat(),
            "timezone": "Asia/Shanghai", "sqlite_integrity_check": "ok", "directories": directories,
            "files": backup_files,
        })
        marker.unlink()
    except BaseException as error:
        code = error.code if isinstance(error, BackupError) else "backup-failed"
        try:
            _write_json(marker, {
                **evidence, "stage": stage, "error_code": code, "error_type": type(error).__name__,
                "failed_at": _now().isoformat(),
            })
        except OSError:
            pass  # The existing incomplete marker still identifies an interrupted backup.
        raise BackupError(code, destination) from None
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up stopped local BLD runtime files with a verified SQLite snapshot.")
    parser.add_argument("--project-dir", type=Path, default=PROJECT_DIR)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT, help="External parent for the new private backup.")
    args = parser.parse_args(argv)
    try:
        destination = create_backup(args.project_dir, args.backup_root)
    except BackupError as error:
        print(json.dumps({
            "status": "incomplete", "error_code": error.code, "message": str(error),
            "backup_dir": str(error.backup_dir) if error.backup_dir else None,
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"status": "complete", "backup_dir": str(destination), "manifest": str(destination / "manifest.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
