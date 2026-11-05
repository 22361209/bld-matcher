from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType

from app.database import connect

from .excel_import import import_materials_from_excel
from .item_store import (
    count_material_items,
    deactivate_material_item,
    get_material_item,
    list_material_items,
    material_filter_options,
    material_item_stats,
    rows_for_material_sheet,
    upsert_material_item,
)
from .weights import costing_material
from app.platform.audit_store import log_event
from app.platform.clock import now_text


class SQLiteMaterialRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def stats(self) -> dict[str, int]:
        return material_item_stats(self.connection)

    def count(self, *, query: str, status: str, column_filters: Mapping[str, tuple[str, ...]] | None = None) -> int:
        return count_material_items(
            self.connection,
            query=query,
            include_inactive=status == "all",
            only_inactive=status == "inactive",
            column_filters=column_filters,
        )

    def list(
        self,
        *,
        query: str,
        status: str,
        limit: int,
        offset: int,
        column_filters: Mapping[str, tuple[str, ...]] | None = None,
    ) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in list_material_items(
                self.connection,
                query=query,
                include_inactive=status == "all",
                only_inactive=status == "inactive",
                limit=limit,
                offset=offset,
                column_filters=column_filters,
            )
        ]

    def costing_candidates(self, code: str) -> list[dict[str, str | None]]:
        rows = self.connection.execute(
            "SELECT * FROM material_items WHERE active = 1 AND code = ? COLLATE NOCASE ORDER BY model, id",
            (code,),
        )
        return [item for row in rows if (item := costing_material(dict(row))) is not None]

    def active_for_weight_backfill(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM material_items WHERE active = 1 ORDER BY model, code, id"
        )]

    def fill_missing_net_weight(self, row: dict, net: str, *, evidence: str, actor: str) -> bool:
        cursor = self.connection.execute(
            """UPDATE material_items SET net_weight=?, updated_at=?
               WHERE id=? AND active=1 AND NULLIF(TRIM(net_weight), '') IS NULL""",
            (net, now_text(), row["id"]),
        )
        if cursor.rowcount != 1:
            return False
        self.audit("补录材料净重", "material_item", f"{row['model']}-{row['code']}",
                   f"{evidence}，单件净重 {net} kg", actor=actor)
        return True

    def filter_options(
        self, *, query: str, status: str, column_filters: Mapping[str, tuple[str, ...]]
    ) -> dict[str, list[dict[str, object]]]:
        return material_filter_options(
            self.connection,
            query=query,
            include_inactive=status == "all",
            only_inactive=status == "inactive",
            column_filters=column_filters,
        )

    def get(self, item_id: int) -> dict[str, object] | None:
        row = get_material_item(self.connection, item_id)
        return dict(row) if row is not None else None

    def save(self, data: Mapping[str, object], *, actor: str) -> int:
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
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
