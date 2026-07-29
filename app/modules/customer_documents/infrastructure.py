from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app.helpers import safe_upload_name

from .domain import CustomerDocumentValidationError
from .file_validation import upload_parts, validate_file_signature, validate_upload_metadata
from .ports import PreparedCustomerFile, PreparedCustomerFileBatch


_SAFE_STORAGE_SEGMENT = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
_MAX_STORAGE_BASENAME_BYTES = 222  # 255-byte component minus UUID and separator.


def _storage_safe_name(original_name: str, suffix: str) -> str:
    safe_name = safe_upload_name(original_name)
    if len(safe_name.encode("utf-8")) <= _MAX_STORAGE_BASENAME_BYTES:
        return safe_name

    safe_suffix = Path(safe_name).suffix or suffix
    stem = safe_name[: -len(safe_suffix)] if safe_suffix else safe_name
    budget = _MAX_STORAGE_BASENAME_BYTES - len(safe_suffix.encode("utf-8"))
    encoded = stem.encode("utf-8")[: max(1, budget)]
    while encoded:
        try:
            shortened = encoded.decode("utf-8").rstrip(". ")
            break
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    else:
        shortened = "file"
    return f"{shortened or 'file'}{safe_suffix}"


class CustomerDocumentFileStore:
    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = 20 * 1024 * 1024,
        max_files_per_version: int = 20,
        max_batch_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.root = root
        self.max_file_bytes = max_file_bytes
        self.max_files_per_version = max_files_per_version
        self.max_batch_bytes = max_batch_bytes

    def prepare(
        self,
        files: Sequence[object],
        *,
        customer_sync_id: str,
        group_sync_id: str,
        version_no: int,
    ) -> PreparedCustomerFileBatch:
        if not files:
            raise CustomerDocumentValidationError(
                "customer_document.files_required", "请至少选择一个文件。", field="files"
            )
        if len(files) > self.max_files_per_version:
            raise CustomerDocumentValidationError(
                "customer_document.too_many_files",
                f"每个版本最多上传 {self.max_files_per_version} 个文件。",
                field="files",
            )
        self._validate_storage_segment(customer_sync_id, "customer sync id")
        self._validate_storage_segment(group_sync_id, "group sync id")
        if version_no < 1:
            raise CustomerDocumentValidationError("customer_document.invalid_version", "文件版本号不正确。")

        prepared: list[PreparedCustomerFile] = []
        total_bytes = 0
        try:
            for upload in files:
                item = self._prepare_one(
                    upload,
                    customer_sync_id=customer_sync_id,
                    group_sync_id=group_sync_id,
                    version_no=version_no,
                )
                prepared.append(item)
                total_bytes += item.size_bytes
                if total_bytes > self.max_batch_bytes:
                    raise CustomerDocumentValidationError(
                        "customer_document.batch_too_large",
                        f"单次上传总大小不能超过 {self.max_batch_bytes // (1024 * 1024)} MB。",
                        field="files",
                    )
        except Exception:
            self.discard(PreparedCustomerFileBatch(tuple(prepared)))
            raise
        return PreparedCustomerFileBatch(tuple(prepared))

    def _prepare_one(
        self,
        upload: object,
        *,
        customer_sync_id: str,
        group_sync_id: str,
        version_no: int,
    ) -> PreparedCustomerFile:
        filename, declared_type, stream = upload_parts(upload)
        original_name, suffix, content_type = validate_upload_metadata(filename, declared_type)
        file_sync_id = uuid4().hex
        safe_name = _storage_safe_name(original_name, suffix)
        storage_path = PurePosixPath(
            customer_sync_id,
            group_sync_id,
            f"v{version_no:04d}",
            f"{file_sync_id}-{safe_name}",
        ).as_posix()
        destination = self._destination(storage_path, customer_sync_id=customer_sync_id)
        staging_dir = self.root / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="customer-document-", suffix=suffix, dir=staging_dir)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size = 0
        try:
            try:
                stream.seek(0)
            except (AttributeError, OSError):
                pass
            with os.fdopen(descriptor, "wb") as handle:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise CustomerDocumentValidationError(
                            "customer_document.invalid_file", "上传文件无法读取。", field="files"
                        )
                    size += len(chunk)
                    if size > self.max_file_bytes:
                        raise CustomerDocumentValidationError(
                            "customer_document.file_too_large",
                            f"单个文件不能超过 {self.max_file_bytes // (1024 * 1024)} MB。",
                            field="files",
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if size == 0:
                raise CustomerDocumentValidationError(
                    "customer_document.empty_file", f"文件 {original_name} 为空。", field="files"
                )
            validate_file_signature(temporary, suffix)
            return PreparedCustomerFile(
                sync_id=file_sync_id,
                original_name=original_name,
                storage_path=storage_path,
                content_type=content_type,
                size_bytes=size,
                sha256=digest.hexdigest(),
                temporary_path=temporary,
                destination_path=destination,
            )
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise

    def promote(self, batch: PreparedCustomerFileBatch) -> None:
        promoted: list[PreparedCustomerFile] = []
        try:
            for item in batch.files:
                item.destination_path.parent.mkdir(parents=True, exist_ok=True)
                if item.destination_path.exists():
                    raise CustomerDocumentValidationError(
                        "customer_document.storage_conflict", "客户资料文件存储冲突，请重试。"
                    )
                os.replace(item.temporary_path, item.destination_path)
                promoted.append(item)
        except Exception:
            self.compensate(PreparedCustomerFileBatch(tuple(promoted)))
            self.discard(batch)
            raise

    def discard(self, batch: PreparedCustomerFileBatch) -> None:
        for item in batch.files:
            item.temporary_path.unlink(missing_ok=True)
        self._prune_empty(self.root / ".staging", stop=self.root)

    def compensate(self, batch: PreparedCustomerFileBatch) -> None:
        for item in batch.files:
            item.destination_path.unlink(missing_ok=True)
            self._prune_empty(item.destination_path.parent, stop=self.root)
            item.temporary_path.unlink(missing_ok=True)
        self._prune_empty(self.root / ".staging", stop=self.root)

    def resolve(self, storage_path: str, *, customer_sync_id: str, group_sync_id: str = "") -> Path:
        self._validate_storage_segment(customer_sync_id, "customer sync id")
        if group_sync_id:
            self._validate_storage_segment(group_sync_id, "group sync id")
        destination = self._destination(
            storage_path,
            customer_sync_id=customer_sync_id,
            group_sync_id=group_sync_id,
        )
        if not destination.is_file():
            raise CustomerDocumentValidationError("customer_document.file_missing", "客户资料文件不存在。")
        return destination

    def verify(self, path: Path, *, size_bytes: int, sha256: str) -> bool:
        try:
            if not path.is_file() or path.stat().st_size != size_bytes:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest() == sha256
        except OSError:
            return False

    def _destination(self, storage_path: str, *, customer_sync_id: str, group_sync_id: str = "") -> Path:
        pure = PurePosixPath(str(storage_path or ""))
        if pure.is_absolute() or not pure.parts or pure.parts[0] != customer_sync_id:
            raise CustomerDocumentValidationError("customer_document.unsafe_path", "客户资料文件路径无效。")
        if group_sync_id and (len(pure.parts) < 2 or pure.parts[1] != group_sync_id):
            raise CustomerDocumentValidationError("customer_document.unsafe_path", "客户资料文件路径无效。")
        if any(part in {"", ".", ".."} for part in pure.parts):
            raise CustomerDocumentValidationError("customer_document.unsafe_path", "客户资料文件路径无效。")
        root = self.root.resolve()
        candidate = (root / Path(*pure.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise CustomerDocumentValidationError(
                "customer_document.unsafe_path", "客户资料文件路径无效。"
            ) from exc
        return candidate

    @staticmethod
    def _validate_storage_segment(value: str, label: str) -> None:
        if not _SAFE_STORAGE_SEGMENT.fullmatch(value):
            raise CustomerDocumentValidationError(
                "customer_document.invalid_storage_identity", f"Invalid {label}."
            )

    @staticmethod
    def _prune_empty(path: Path, *, stop: Path | None = None) -> None:
        boundary = stop.resolve() if stop else None
        current = path
        while current.exists() and current.is_dir() and (boundary is None or current.resolve() != boundary):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
