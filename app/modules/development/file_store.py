from __future__ import annotations

import hashlib
import fcntl
import logging
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

from .document_domain import DevelopmentDocumentError, identity
from .file_types import PreparedDevelopmentFile
from .file_validation import CONTENT_TYPES, upload_metadata, validate_signature


logger = logging.getLogger(__name__)
ALLOWED_EXTENSIONS = frozenset(CONTENT_TYPES)
MAX_FILE_BYTES = 50 * 1024 * 1024


class DevelopmentFileStore:
    """Immutable, content-checked files; compensation only owns files this instance created."""

    def __init__(self, root: Path, *, max_file_bytes: int = MAX_FILE_BYTES) -> None:
        self.root = Path(root)
        self.max_file_bytes = max_file_bytes

    def prepare(
        self, upload: object, *, project_id: str, revision_id: str, expected_sha256: str | None = None,
    ) -> PreparedDevelopmentFile:
        identity(project_id)
        identity(revision_id)
        filename = str(getattr(upload, "filename", "") or "")
        declared_type = str(getattr(upload, "content_type", "") or "")
        original_name, extension, content_type = upload_metadata(filename, declared_type)
        stream = getattr(upload, "stream", upload)
        if not callable(getattr(stream, "read", None)):
            raise DevelopmentDocumentError("development.file_unreadable", "无法读取上传文件。")
        stream = cast(BinaryIO, stream)
        staging = self._safe_path(PurePosixPath(".staging"))
        staging.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="revision-", suffix=extension, dir=staging)
        temporary = Path(name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise DevelopmentDocumentError("development.file_unreadable", "无法读取上传文件。")
                    size += len(chunk)
                    if size > self.max_file_bytes:
                        raise DevelopmentDocumentError("development.file_too_large", "文件超过允许的上传大小。")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if not size:
                raise DevelopmentDocumentError("development.file_empty", "上传文件不能为空。")
            validate_signature(temporary, extension)
            checksum = digest.hexdigest()
            if expected_sha256 is not None and checksum != expected_sha256:
                raise DevelopmentDocumentError("development.file_digest", "文件校验值不一致，请重新预览。")
            storage_path = f"{project_id}/{revision_id}/{checksum}{extension}"
            return PreparedDevelopmentFile(
                revision_id, original_name, extension, content_type, size, checksum, storage_path,
                temporary, self.resolve(storage_path, require_exists=False),
            )
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def promote(self, prepared: PreparedDevelopmentFile) -> bool:
        lock_path = self._safe_path(PurePosixPath(".publish.lock"))
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return self._promote_locked(prepared)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _promote_locked(self, prepared: PreparedDevelopmentFile) -> bool:
        destination = self.resolve(prepared.storage_path, require_exists=False)
        if not self.verify(prepared.temporary_path, size_bytes=prepared.size_bytes, sha256=prepared.sha256):
            raise DevelopmentDocumentError("development.file_digest", "待保存文件已改变，请重新上传。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A revision UUID identifies one immutable file even if its proposed hash/name differs.
        existing = list(destination.parent.iterdir())
        if existing:
            if len(existing) != 1 or existing[0] != destination or not self.verify(
                destination, size_bytes=prepared.size_bytes, sha256=prepared.sha256,
            ):
                raise DevelopmentDocumentError("development.file_conflict", "相同修订标识已有不同文件，拒绝覆盖。")
            self.discard(prepared)
            return False
        try:
            # Atomic publication without os.replace's overwrite race, on the same filesystem.
            os.link(prepared.temporary_path, destination)
        except FileExistsError as exc:
            if not self.verify(destination, size_bytes=prepared.size_bytes, sha256=prepared.sha256):
                raise DevelopmentDocumentError("development.file_conflict", "修订文件冲突，拒绝覆盖。") from exc
            self.discard(prepared)
            return False
        prepared.created = True
        stat = destination.stat()
        prepared.created_identity = (stat.st_dev, stat.st_ino)
        self.discard(prepared)
        return True

    @staticmethod
    def discard(prepared: PreparedDevelopmentFile) -> None:
        prepared.temporary_path.unlink(missing_ok=True)

    def compensate(self, prepared: PreparedDevelopmentFile) -> None:
        self.discard(prepared)
        if not prepared.created:
            return
        destination = self.resolve(prepared.storage_path, require_exists=False)
        if not destination.exists():
            return
        stat = destination.stat()
        if (stat.st_dev, stat.st_ino) != prepared.created_identity:
            logger.error("Development file compensation refused replacement", extra={"revision_id": prepared.revision_id})
            raise DevelopmentDocumentError("development.file_compensation", "文件回滚发生冲突，请联系管理员。")
        destination.unlink()
        prepared.created = False
        for directory in (destination.parent, destination.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break

    def resolve(self, storage_path: str, *, require_exists: bool = True) -> Path:
        pure = PurePosixPath(storage_path)
        if pure.is_absolute() or len(pure.parts) != 3 or "\\" in storage_path:
            raise DevelopmentDocumentError("development.file_path", "文件引用无效。")
        identity(pure.parts[0])
        identity(pure.parts[1])
        basename = pure.parts[2]
        suffix = PurePosixPath(basename).suffix
        if suffix not in CONTENT_TYPES or not re.fullmatch(r"[a-f0-9]{64}", basename[:-len(suffix)]):
            raise DevelopmentDocumentError("development.file_path", "文件引用无效。")
        destination = self._safe_path(pure)
        if require_exists and not destination.is_file():
            raise DevelopmentDocumentError("development.file_missing", "修订文件不存在。")
        if destination.exists() and destination.stat().st_nlink != 1:
            raise DevelopmentDocumentError("development.file_path", "文件引用无效。")
        return destination

    def _safe_path(self, pure: PurePosixPath) -> Path:
        if self.root.is_symlink() or any(part in {"", ".", ".."} for part in pure.parts):
            raise DevelopmentDocumentError("development.file_path", "文件引用无效。")
        root = self.root.resolve()
        candidate = root
        for part in pure.parts:
            candidate /= part
            if candidate.is_symlink():
                raise DevelopmentDocumentError("development.file_path", "文件引用无效。")
        if not candidate.resolve().is_relative_to(root):
            raise DevelopmentDocumentError("development.file_path", "文件引用无效。")
        return candidate

    @staticmethod
    def verify(path: Path, *, size_bytes: int, sha256: str) -> bool:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size != size_bytes or path.stat().st_nlink != 1:
                return False
            with path.open("rb") as handle:
                return hashlib.file_digest(handle, "sha256").hexdigest() == sha256
        except OSError:
            return False


def legacy_source(root: Path, relative_name: str) -> Path:
    """Only callers' configured legacy root is accepted; no absolute paths or symlinks."""
    pure = PurePosixPath(relative_name)
    if pure.is_absolute() or "\\" in relative_name or any(part in {".", ".."} for part in pure.parts):
        raise DevelopmentDocumentError("development.legacy_path", "旧图纸引用无效。")
    candidate = root
    for part in pure.parts:
        candidate /= part
        if candidate.is_symlink():
            raise DevelopmentDocumentError("development.legacy_path", "旧图纸引用无效。")
    if (root.is_symlink() or not candidate.resolve().is_relative_to(root.resolve())
            or not candidate.is_file() or candidate.stat().st_nlink != 1):
        raise DevelopmentDocumentError("development.legacy_missing", "旧图纸不存在或超出允许目录。")
    return candidate
