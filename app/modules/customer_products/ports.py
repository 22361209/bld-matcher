from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .domain import (
    CatalogProductInfo,
    CustomerDrawingFile,
    CustomerDrawingFileReference,
    CustomerDrawingSlot,
    CustomerDrawingSummary,
    CustomerIdentity,
    CustomerProduct,
    QuotedProductOption,
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
class StagedCustomerFileRemoval:
    original_path: Path = field(repr=False)
    staged_path: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class StagedCustomerFileRemovalBatch:
    files: tuple[StagedCustomerFileRemoval, ...]


@dataclass(frozen=True, slots=True)
class CustomerFileRemovalFailure:
    original_path: Path = field(repr=False)
    staged_path: Path = field(repr=False)
    error: Exception = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class CustomerFileRemovalTarget:
    storage_path: str = field(repr=False)
    group_sync_id: str


@dataclass(frozen=True, slots=True)
class CustomerFilePayload:
    path: Path = field(repr=False)
    download_name: str
    content_type: str
    size_bytes: int
    sha256: str
    previewable: bool


@dataclass(frozen=True, slots=True)
class CatalogDrawingSource:
    path: Path = field(repr=False)
    original_name: str = ""


@dataclass(frozen=True, slots=True)
class CustomerDrawingFileAccess:
    file: CustomerDrawingFile
    customer_id: int
    customer_sync_id: str
    group_sync_id: str


class CustomerProductRepository(Protocol):
    def customer_identity(self, customer_id: int) -> CustomerIdentity | None: ...

    def list_products(self, customer_id: int) -> list[CustomerProduct]: ...

    def get_product(self, customer_id: int, product_id: int) -> CustomerProduct | None: ...

    def insert_product(
        self,
        *,
        customer_id: int,
        sync_id: str,
        bld_no: str,
        customer_product_code: str,
        customer_product_name: str,
        actor: str,
    ) -> int: ...

    def update_product(
        self,
        customer_id: int,
        product_id: int,
        *,
        customer_product_code: str,
        customer_product_name: str,
        actor: str,
    ) -> bool: ...

    def lock_product_for_delete(self, customer_id: int, product_id: int) -> CustomerProduct | None: ...

    def drawing_group_sync_ids(self, customer_id: int) -> set[str]: ...

    def delete_product(self, customer_id: int, product_id: int) -> bool: ...

    def get_slot(self, customer_id: int, product_id: int, kind: str) -> CustomerDrawingSlot | None: ...

    def insert_slot(
        self,
        *,
        customer_product_id: int,
        customer_id: int,
        sync_id: str,
        kind: str,
        actor: str,
    ) -> int: ...

    def claim_next_version(
        self,
        customer_id: int,
        group_id: int,
        *,
        expected_version: int,
        new_version: int,
        actor: str,
    ) -> bool: ...

    def set_current_version(self, customer_id: int, group_id: int, version_no: int, *, actor: str) -> bool: ...

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

    def get_file_access(self, customer_id: int, file_id: int) -> CustomerDrawingFileAccess | None: ...

    def file_references(self, file_ids: Sequence[int]) -> dict[int, CustomerDrawingFileReference]: ...

    def summaries(self, customer_ids: Sequence[int]) -> dict[int, CustomerDrawingSummary]: ...

    def audit(self, action: str, target_key: str, detail: str, *, actor: str) -> None: ...


class CustomerProductUnitOfWork(Protocol):
    @property
    def repository(self) -> CustomerProductRepository: ...

    def __enter__(self) -> CustomerProductUnitOfWork: ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...


CustomerProductUnitOfWorkFactory = Callable[[], CustomerProductUnitOfWork]


class QuoteHistoryPort(Protocol):
    def quoted_products(self, customer_id: int, customer_name: str) -> list[QuotedProductOption]: ...


class ProductCatalogPort(Protocol):
    def info(self, bld_no: str) -> CatalogProductInfo | None: ...

    def drawing_source(self, bld_no: str) -> CatalogDrawingSource | None: ...


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

    def stage_removal(
        self,
        targets: Sequence[CustomerFileRemovalTarget],
        *,
        customer_sync_id: str,
        live_group_sync_ids: Collection[str],
    ) -> StagedCustomerFileRemovalBatch: ...

    def restore_removal(
        self, batch: StagedCustomerFileRemovalBatch
    ) -> tuple[CustomerFileRemovalFailure, ...]: ...

    def finalize_removal(
        self, batch: StagedCustomerFileRemovalBatch
    ) -> tuple[CustomerFileRemovalFailure, ...]: ...

    def resolve(self, storage_path: str, *, customer_sync_id: str, group_sync_id: str = "") -> Path: ...

    def verify(self, path: Path, *, size_bytes: int, sha256: str) -> bool: ...
