from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType

from app.database import (
    connect,
    count_material_items,
    deactivate_material_item,
    get_material_item,
    import_materials_from_excel,
    list_material_items,
    log_event,
    material_item_stats,
    rows_for_material_sheet,
    upsert_material_item,
)


class SQLiteMaterialRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def stats(self) -> dict[str, int]:
        return material_item_stats(self.connection)

    def count(self, *, query: str, status: str) -> int:
        return count_material_items(
            self.connection,
            query=query,
            include_inactive=status == "all",
            only_inactive=status == "inactive",
        )

    def list(self, *, query: str, status: str, limit: int, offset: int) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in list_material_items(
                self.connection,
                query=query,
                include_inactive=status == "all",
                only_inactive=status == "inactive",
                limit=limit,
                offset=offset,
            )
        ]

    def get(self, item_id: int) -> dict[str, object] | None:
        row = get_material_item(self.connection, item_id)
        return dict(row) if row is not None else None

    def save(self, data: Mapping[str, object], *, actor: str) -> int:
        return upsert_material_item(self.connection, dict(data), actor=actor, commit=False)

    def deactivate(self, item_id: int, *, actor: str) -> None:
        deactivate_material_item(self.connection, item_id, actor=actor, commit=False)

    def sheet_rows(self) -> dict[str, list[dict]]:
        return rows_for_material_sheet(self.connection)

    def import_data(self, path: Path, *, actor: str) -> int:
        return import_materials_from_excel(
            self.connection,
            path,
            replace=True,
            actor=actor,
            commit=False,
        )

    def audit(self, action: str, target_type: str, target_key: str, detail: str, *, actor: str) -> None:
        log_event(
            self.connection,
            action,
            target_type,
            target_key,
            detail,
            actor=actor,
        )


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteMaterialUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.connection: sqlite3.Connection | None = None
        self.repository: SQLiteMaterialRepository
        self._committed = False

    def __enter__(self) -> SQLiteMaterialUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteMaterialRepository(self.connection)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Material unit of work is not active.")
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
