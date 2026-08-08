from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from werkzeug.datastructures import FileStorage

from app.database import connect
from app.drawings import save_product_drawing
from app.platform.audit_store import log_event
from app.product_status import canonical_product_status

from .repository_catalog import ProductCatalogRepositoryMixin
from .repository_media import ProductMediaRepositoryMixin
from .repository_media_transactions import NewProductMediaTransaction, ProductRenameMediaTransaction
from .repository_options import ProductOptionRepositoryMixin
from .ports import ProductRepository
from .repository_queries import ProductQueryRepositoryMixin
from .repository_writes import ProductWriteRepositoryMixin


class SQLiteProductRepository(
    ProductQueryRepositoryMixin,
    ProductWriteRepositoryMixin,
    ProductMediaRepositoryMixin,
    ProductOptionRepositoryMixin,
    ProductCatalogRepositoryMixin,
):
    """Compatibility facade composed from focused SQLite repository responsibilities."""

    def __init__(self, connection: sqlite3.Connection, database_path: Path) -> None:
        self.connection = connection
        self.database_path = database_path
        self._copy_media_transaction: NewProductMediaTransaction | None = None
        self._rename_media_transaction: ProductRenameMediaTransaction | None = None
        self.connection.create_function(
            "PRODUCT_STATUS_KEY",
            1,
            canonical_product_status,
            deterministic=True,
        )

    def _save_product_drawing(
        self,
        connection: sqlite3.Connection,
        product: sqlite3.Row,
        file: FileStorage,
        *,
        commit: bool,
    ) -> Path:
        # Resolve the module global at call time to preserve the established
        # app.modules.products.repository.save_product_drawing patch point.
        return save_product_drawing(connection, product, file, commit=commit)

    def _log_event(
        self,
        connection: sqlite3.Connection,
        action: str,
        target_type: str,
        target_key: str,
        detail: str = "",
        actor: str = "",
    ) -> None:
        # Keep the historical repository.log_event fault-injection path live.
        log_event(connection, action, target_type, target_key, detail, actor)


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteProductUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.connection: sqlite3.Connection | None = None
        self.repository: ProductRepository
        self._committed = False

    def __enter__(self) -> SQLiteProductUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteProductRepository(self.connection, self.database_path)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Product unit of work is not active.")
        self.connection.commit()
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.connection is None:
            return
        if exc_type is not None or not self._committed:
            self.connection.rollback()
        self.connection.close()
        self.connection = None
