from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .domain import (
    CustomerDrawingFile,
    CustomerDrawingFileReference,
    CustomerDrawingGroup,
    CustomerDrawingSummary,
    CustomerIdentity,
)


@dataclass(frozen=True, slots=True)
class PreparedCustomerFile:
    sync_id: str
    original_name: str
    storage_path: str
    content_type: str
    size_bytes: int
    sha256: str
    temporary_path: Path = field(repr=False)
    destination_path: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedCustomerFileBatch:
    files: tuple[PreparedCustomerFile, ...]


@dataclass(frozen=True, slots=True)
class CustomerFilePayload:
    path: Path = field(repr=False)
    download_name: str
    content_type: str
    size_bytes: int
    sha256: str
    previewable: bool


@dataclass(frozen=True, slots=True)
class CustomerDrawingFileAccess:
    file: CustomerDrawingFile
    customer_id: int
    customer_sync_id: str
    group_sync_id: str
    group_archived: bool


class CustomerDrawingRepository(Protocol):
    def customer_identity(self, customer_id: int) -> CustomerIdentity | None: ...

    def list_groups(self, customer_id: int, *, include_archived: bool) -> list[CustomerDrawingGroup]: ...

    def get_group(self, customer_id: int, group_id: int) -> CustomerDrawingGroup | None: ...

    def insert_group(
        self,
        *,
        customer_id: int,
        sync_id: str,
        direction: str,
        bld_no: str,
        title: str,
        drawing_no: str,
        current_version: int,
        actor: str,
    ) -> int: ...

    def update_group(
        self,
        customer_id: int,
        group_id: int,
        *,
        direction: str,
        bld_no: str,
        title: str,
        drawing_no: str,
        actor: str,
    ) -> bool: ...

    def claim_next_version(
        self,
        customer_id: int,
        group_id: int,
        *,
        expected_version: int,
        actor: str,
    ) -> int | None: ...

    def insert_file(
        self,
        group_id: int,
        version_no: int,
        file: PreparedCustomerFile,
        *,
        revision_label: str,
        note: str,
        actor: str,
    ) -> None: ...

    def archive_group(self, customer_id: int, group_id: int, *, actor: str) -> bool: ...

    def unarchive_group(self, customer_id: int, group_id: int, *, actor: str) -> bool: ...

    def get_file_access(self, customer_id: int, file_id: int) -> CustomerDrawingFileAccess | None: ...

    def file_references(self, file_ids: Sequence[int]) -> dict[int, CustomerDrawingFileReference]: ...

    def summaries(self, customer_ids: Sequence[int]) -> dict[int, CustomerDrawingSummary]: ...

    def audit(self, action: str, target_key: str, detail: str, *, actor: str) -> None: ...


class CustomerDrawingUnitOfWork(Protocol):
    @property
    def repository(self) -> CustomerDrawingRepository: ...

    def __enter__(self) -> CustomerDrawingUnitOfWork: ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...


CustomerDrawingUnitOfWorkFactory = Callable[[], CustomerDrawingUnitOfWork]


class CustomerDrawingStorage(Protocol):
    def prepare(
        self,
        files: Sequence[object],
        *,
        customer_sync_id: str,
        group_sync_id: str,
        version_no: int,
    ) -> PreparedCustomerFileBatch: ...

    def promote(self, batch: PreparedCustomerFileBatch) -> None: ...

    def discard(self, batch: PreparedCustomerFileBatch) -> None: ...

    def compensate(self, batch: PreparedCustomerFileBatch) -> None: ...

    def resolve(self, storage_path: str, *, customer_sync_id: str, group_sync_id: str = "") -> Path: ...

    def verify(self, path: Path, *, size_bytes: int, sha256: str) -> bool: ...
