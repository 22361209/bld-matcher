from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .domain import CustomerDocumentFile, CustomerDocumentGroup, CustomerDocumentSummary, CustomerIdentity


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
class CustomerDocumentFileAccess:
    file: CustomerDocumentFile
    customer_id: int
    customer_sync_id: str
    group_sync_id: str
    group_archived: bool


class CustomerDocumentRepository(Protocol):
    def customer_identity(self, customer_id: int) -> CustomerIdentity | None: ...

    def list_groups(self, customer_id: int, *, include_archived: bool) -> list[CustomerDocumentGroup]: ...

    def get_group(self, customer_id: int, group_id: int) -> CustomerDocumentGroup | None: ...

    def insert_group(
        self,
        *,
        customer_id: int,
        sync_id: str,
        category: str,
        title: str,
        description: str,
        language: str,
        current_version: int,
        actor: str,
    ) -> int: ...

    def update_group(
        self,
        customer_id: int,
        group_id: int,
        *,
        category: str,
        title: str,
        description: str,
        language: str,
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

    def insert_files(
        self,
        group_id: int,
        version_no: int,
        files: Iterable[PreparedCustomerFile],
        *,
        actor: str,
    ) -> None: ...

    def archive_group(self, customer_id: int, group_id: int, *, actor: str) -> bool: ...

    def get_file_access(self, customer_id: int, file_id: int) -> CustomerDocumentFileAccess | None: ...

    def summaries(self, customer_ids: Sequence[int]) -> dict[int, CustomerDocumentSummary]: ...

    def audit(self, action: str, target_key: str, detail: str, *, actor: str) -> None: ...


class CustomerDocumentUnitOfWork(Protocol):
    @property
    def repository(self) -> CustomerDocumentRepository: ...

    def __enter__(self) -> CustomerDocumentUnitOfWork: ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...


CustomerDocumentUnitOfWorkFactory = Callable[[], CustomerDocumentUnitOfWork]


class CustomerDocumentStorage(Protocol):
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
