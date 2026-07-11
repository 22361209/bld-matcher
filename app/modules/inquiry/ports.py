from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, Self

from app.matcher import ProductCatalog


class InquiryRepository(Protocol):
    def audit(self, action: str, target_type: str, target_key: str, detail: str, *, actor: str) -> None: ...

    def save_alias(self, source_code: str, bld_no: str, note: str, *, actor: str) -> None: ...

    def append_product_code(self, bld_no: str, code: str, target: str, *, actor: str) -> bool: ...

    def delete_alias(self, source_code: str, *, actor: str) -> None: ...

    def build_drawings(self, rows: list[dict], output_path: Path) -> dict: ...


class InquiryUnitOfWork(Protocol):
    repository: InquiryRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...


InquiryUnitOfWorkFactory = Callable[[], InquiryUnitOfWork]


class CatalogProvider(Protocol):
    def catalog(self) -> ProductCatalog | None: ...

    def invalidate_catalog(self) -> None: ...
