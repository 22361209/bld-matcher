from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import ContextManager, Protocol, Self

from .domain import QuoteDraft, QuoteFilters, QuoteRecord, QuoteStats


class QuoteRepository(Protocol):
    def add(self, draft: QuoteDraft) -> QuoteRecord: ...

    def get(self, quote_id: int) -> QuoteRecord | None: ...

    def list(self, filters: QuoteFilters, *, limit: int, offset: int) -> list[QuoteRecord]: ...

    def count(self, filters: QuoteFilters) -> int: ...

    def latest(self, *, customer_name: str, bld_no: str) -> QuoteRecord | None: ...

    def update(self, quote_id: int, draft: QuoteDraft, *, expected_version: int) -> QuoteRecord | None: ...

    def add_revision(self, before: QuoteRecord, after: QuoteRecord, *, actor: str) -> None: ...

    def stats(self) -> QuoteStats: ...

    def audit(self, action: str, quote: QuoteRecord, *, actor: str) -> None: ...


class QuoteUnitOfWork(Protocol):
    repository: QuoteRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...


QuoteUnitOfWorkFactory = Callable[[], QuoteUnitOfWork]


class QuoteImportPort(Protocol):
    def parse(self, path: Path, *, customer_name: str, currency: str) -> dict: ...

    def encode(self, rows: list[dict]) -> str: ...

    def decode(self, payload: str) -> list[dict]: ...


class ImportLockBusyError(RuntimeError):
    pass


class ImportLockPort(Protocol):
    def __call__(self, owner: str, purpose: str) -> ContextManager[None]: ...


QuoteRows = Iterable[dict[str, object]]
