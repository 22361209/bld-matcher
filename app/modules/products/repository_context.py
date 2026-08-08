from __future__ import annotations

import sqlite3
from pathlib import Path

from werkzeug.datastructures import FileStorage

from .domain import ProductFilters, ProductRecord


class ProductRepositoryContext:
    """Typed collaboration surface shared by the repository responsibility mixins."""

    connection: sqlite3.Connection
    database_path: Path

    def _rows(
        self,
        filters: ProductFilters,
        *,
        limit: int | None,
        offset: int = 0,
        sort_by: str = "bld",
    ) -> list[sqlite3.Row]:
        raise NotImplementedError

    def get(self, product_id: int) -> ProductRecord | None:
        raise NotImplementedError

    def get_by_bld(self, bld_no: str) -> ProductRecord | None:
        raise NotImplementedError

    def backup_database(self, target_path: Path) -> None:
        raise NotImplementedError

    def save_image(self, product_id: int, file: object, *, slot: int, actor: str) -> ProductRecord:
        raise NotImplementedError

    def save_drawing(self, product_id: int, file: object, *, actor: str) -> ProductRecord:
        raise NotImplementedError

    def _save_product_drawing(
        self,
        connection: sqlite3.Connection,
        product: sqlite3.Row,
        file: FileStorage,
        *,
        commit: bool,
    ) -> Path:
        raise NotImplementedError

    def _log_event(
        self,
        connection: sqlite3.Connection,
        action: str,
        target_type: str,
        target_key: str,
        detail: str = "",
        actor: str = "",
    ) -> None:
        raise NotImplementedError
