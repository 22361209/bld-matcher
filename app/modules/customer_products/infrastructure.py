from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from collections.abc import Collection, Sequence
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app.helpers import safe_upload_name

from .domain import (
    CatalogProductInfo,
    CustomerProductValidationError,
    QuotedProductOption,
)
from .file_validation import upload_parts, validate_file_signature, validate_upload_metadata
from .ports import (
    CatalogDrawingSource,
    CustomerFileRemovalFailure,
    CustomerFileRemovalTarget,
    PreparedCustomerFile,
    PreparedCustomerFileBatch,
    StagedCustomerFileRemoval,
    StagedCustomerFileRemovalBatch,
)


logger = logging.getLogger(__name__)


_SAFE_STORAGE_SEGMENT = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
_MAX_STORAGE_BASENAME_BYTES = 222  # 255-byte component minus UUID and separator.
_DRAWINGS_SEGMENT = "drawings"


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


class QuoteHistoryAdapter:
    """惰性调用 quotes 模块：读取客户报价历史中的候选商品。"""

    def quoted_products(self, customer_id: int, customer_name: str) -> list[QuotedProductOption]:
        from app.modules.quotes.factory import get_quote_service

        rows = get_quote_service().customer_product_options(customer_id, customer_name)
        return [
            QuotedProductOption(
                bld_no=str(row.get("bld_no") or ""),
                customer_product_code=str(row.get("customer_product_code") or ""),
            )
            for row in rows
            if str(row.get("bld_no") or "").strip()
        ]


class ProductCatalogAdapter:
    """惰性调用 products 模块：目录品名、首图与目录图纸来源。"""

    def info(self, bld_no: str) -> CatalogProductInfo | None:
        from app.drawings import product_drawing_path
        from app.helpers import product_image_thumb_url, product_image_url
        from app.modules.products.factory import get_product_service

        record = get_product_service().find_by_bld(bld_no, active_only=False)
        if record is None:
            return None
        payload = record.web_payload()
        return CatalogProductInfo(
            bld_no=record.bld_no,
            item_name=record.item,
            image_url=product_image_url(payload),
            thumb_url=product_image_thumb_url(payload),
            has_drawing=product_drawing_path(payload) is not None,
        )

    def drawing_source(self, bld_no: str) -> CatalogDrawingSource | None:
        from app.drawings import product_drawing_path
        from app.modules.products.factory import get_product_service

        record = get_product_service().find_by_bld(bld_no, active_only=False)
        if record is None:
            return None
        path = product_drawing_path(record.web_payload())
        if path is None:
            return None
        return CatalogDrawingSource(path=path, original_name=record.drawing_original_name or path.name)


class CustomerDrawingFileStore:
    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self.root = root
        self.max_file_bytes = max_file_bytes

    def prepare(
        self,
        files: Sequence[object],
        *,
        customer_sync_id: str,
        group_sync_id: str,
        version_no: int,
    ) -> PreparedCustomerFileBatch:
        if not files:
            raise CustomerProductValidationError(
                "customer_drawing.files_required", "请选择一个图纸文件。", field="files"
            )
        if len(files) > 1:
            raise CustomerProductValidationError(
                "customer_drawing.too_many_files",
                "每个版本只能上传一个图纸文件。",
                field="files",
            )
        self._validate_storage_segment(customer_sync_id, "customer sync id")
        self._validate_storage_segment(group_sync_id, "group sync id")
        if version_no < 1:
            raise CustomerProductValidationError("customer_drawing.invalid_version", "文件版本号不正确。")

        prepared: list[PreparedCustomerFile] = []
        try:
            for upload in files:
                prepared.append(
                    self._prepare_one(
                        upload,
                        customer_sync_id=customer_sync_id,
                        group_sync_id=group_sync_id,
                        version_no=version_no,
                    )
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
            _DRAWINGS_SEGMENT,
            group_sync_id,
            f"v{version_no:04d}",
            f"{file_sync_id}-{safe_name}",
        ).as_posix()
        destination = self._destination(storage_path, customer_sync_id=customer_sync_id)
        staging_dir = self.root / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="customer-drawing-", suffix=suffix, dir=staging_dir)
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
                        raise CustomerProductValidationError(
                            "customer_drawing.invalid_file", "上传文件无法读取。", field="files"
                        )
                    size += len(chunk)
                    if size > self.max_file_bytes:
                        raise CustomerProductValidationError(
                            "customer_drawing.file_too_large",
                            f"单个文件不能超过 {self.max_file_bytes // (1024 * 1024)} MB。",
                            field="files",
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if size == 0:
                raise CustomerProductValidationError(
                    "customer_drawing.empty_file", f"文件 {original_name} 为空。", field="files"
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
                    raise CustomerProductValidationError(
                        "customer_drawing.storage_conflict", "图纸文件存储冲突，请重试。"
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

    def stage_removal(
        self,
        targets: Sequence[CustomerFileRemovalTarget],
        *,
        customer_sync_id: str,
        live_group_sync_ids: Collection[str],
    ) -> StagedCustomerFileRemovalBatch:
        self._validate_storage_segment(customer_sync_id, "customer sync id")
        normalized_live_groups = frozenset(str(value) for value in live_group_sync_ids)
        for group_sync_id in normalized_live_groups:
            self._validate_storage_segment(group_sync_id, "group sync id")
        batch_root = self.root / ".deleting" / uuid4().hex
        staged: list[StagedCustomerFileRemoval] = []
        try:
            for target in dict.fromkeys(targets):
                self._validate_storage_segment(target.group_sync_id, "group sync id")
                pure = PurePosixPath(str(target.storage_path or ""))
                if (
                    len(pure.parts) < 4
                    or pure.parts[0] != customer_sync_id
                    or pure.parts[1] != _DRAWINGS_SEGMENT
                ):
                    raise CustomerProductValidationError("customer_drawing.unsafe_path", "图纸文件路径无效。")
                stored_group_sync_id = pure.parts[2]
                self._validate_storage_segment(stored_group_sync_id, "stored group sync id")
                # 036 迁移会把重复旧图纸组合并到保留组，但不移动既有磁盘文件。
                # 因而允许不再存活的旧组目录；绝不允许越界到另一个仍存活的图纸位。
                if (
                    stored_group_sync_id != target.group_sync_id
                    and stored_group_sync_id in normalized_live_groups
                ):
                    raise CustomerProductValidationError("customer_drawing.unsafe_path", "图纸文件路径无效。")
                original = self._destination(
                    target.storage_path,
                    customer_sync_id=customer_sync_id,
                    group_sync_id=stored_group_sync_id,
                )
                if not original.exists():
                    continue
                if not original.is_file():
                    raise CustomerProductValidationError(
                        "customer_drawing.invalid_file_path", "图纸文件路径无效。"
                    )
                relative = original.relative_to(self.root.resolve())
                staged_path = batch_root / relative
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(original, staged_path)
                staged.append(StagedCustomerFileRemoval(original_path=original, staged_path=staged_path))
                self._prune_empty(original.parent, stop=self.root)
            return StagedCustomerFileRemovalBatch(tuple(staged))
        except Exception as exc:
            failures = self.restore_removal(StagedCustomerFileRemovalBatch(tuple(staged)))
            if failures:
                self._log_removal_failures(
                    "Customer drawing staging failed and staged file restore was incomplete",
                    failures,
                )
                raise CustomerProductValidationError(
                    "customer_drawing.restore_incomplete",
                    f"图纸文件暂存失败，且有 {len(failures)} 个文件恢复不完整，请联系管理员处理。",
                ) from exc
            raise

    def restore_removal(
        self, batch: StagedCustomerFileRemovalBatch
    ) -> tuple[CustomerFileRemovalFailure, ...]:
        failures: list[CustomerFileRemovalFailure] = []
        for item in reversed(batch.files):
            if not item.staged_path.exists():
                continue
            if item.original_path.exists():
                failures.append(
                    CustomerFileRemovalFailure(
                        original_path=item.original_path,
                        staged_path=item.staged_path,
                        error=CustomerProductValidationError(
                            "customer_drawing.restore_conflict", "图纸文件恢复冲突，请联系管理员。"
                        ),
                    )
                )
                continue
            try:
                item.original_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(item.staged_path, item.original_path)
                self._prune_empty(item.staged_path.parent, stop=self.root)
            except OSError as exc:
                failures.append(
                    CustomerFileRemovalFailure(
                        original_path=item.original_path,
                        staged_path=item.staged_path,
                        error=exc,
                    )
                )
        self._prune_empty(self.root / ".deleting", stop=self.root)
        return tuple(failures)

    def finalize_removal(
        self, batch: StagedCustomerFileRemovalBatch
    ) -> tuple[CustomerFileRemovalFailure, ...]:
        failures: list[CustomerFileRemovalFailure] = []
        for item in batch.files:
            try:
                item.staged_path.unlink(missing_ok=True)
                self._prune_empty(item.staged_path.parent, stop=self.root)
            except OSError as exc:
                failures.append(
                    CustomerFileRemovalFailure(
                        original_path=item.original_path,
                        staged_path=item.staged_path,
                        error=exc,
                    )
                )
        self._prune_empty(self.root / ".deleting", stop=self.root)
        return tuple(failures)

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
            raise CustomerProductValidationError("customer_drawing.file_missing", "图纸文件不存在。")
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
            raise CustomerProductValidationError("customer_drawing.unsafe_path", "图纸文件路径无效。")
        if group_sync_id and (
            len(pure.parts) < 3 or pure.parts[1] != _DRAWINGS_SEGMENT or pure.parts[2] != group_sync_id
        ):
            raise CustomerProductValidationError("customer_drawing.unsafe_path", "图纸文件路径无效。")
        if any(part in {"", ".", ".."} for part in pure.parts):
            raise CustomerProductValidationError("customer_drawing.unsafe_path", "图纸文件路径无效。")
        root = self.root.resolve()
        lexical_candidate = root / Path(*pure.parts)
        current = root
        for part in pure.parts:
            current /= part
            if current.is_symlink():
                raise CustomerProductValidationError("customer_drawing.unsafe_path", "图纸文件路径无效。")
        candidate = lexical_candidate.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise CustomerProductValidationError("customer_drawing.unsafe_path", "图纸文件路径无效。") from exc
        if group_sync_id:
            group_root = root / customer_sync_id / _DRAWINGS_SEGMENT / group_sync_id
            try:
                candidate.relative_to(group_root)
            except ValueError as exc:
                raise CustomerProductValidationError("customer_drawing.unsafe_path", "图纸文件路径无效。") from exc
        return candidate

    @staticmethod
    def _log_removal_failures(
        message: str,
        failures: Sequence[CustomerFileRemovalFailure],
    ) -> None:
        for failure in failures:
            error = failure.error
            logger.error(
                message,
                exc_info=(type(error), error, error.__traceback__),
                extra={
                    "original_path": str(failure.original_path),
                    "staged_path": str(failure.staged_path),
                },
            )

    @staticmethod
    def _validate_storage_segment(value: str, label: str) -> None:
        if not _SAFE_STORAGE_SEGMENT.fullmatch(value):
            raise CustomerProductValidationError(
                "customer_drawing.invalid_storage_identity", f"Invalid {label}."
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
