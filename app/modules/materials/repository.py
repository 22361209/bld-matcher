from __future__ import annotations

import sqlite3
from hashlib import sha256
import json
from typing import Any
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType

from app.database import connect

from .excel_import import read_material_rows, insert_material_rows
from .item_store import (
    count_material_items,
    deactivate_material_item,
    get_material_item,
    list_material_items,
    material_filter_options,
    upsert_material_item,
)
from .bom_query import MaterialBOM
from .bom_store import migrate_rows, update_bom
from app.platform.audit_store import log_event
from app.platform.clock import now_text


class SQLiteMaterialRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def stats(self) -> dict[str, int]:
        graph = MaterialBOM(self.connection)
        statuses = [r["active"] for links in graph.children.values() for r in links] + [r["active"] for r in graph.legacy]
        models = {k for k, links in graph.children.items() if any(r["active"] for r in links)}
        models.update(r["model"] for r in graph.legacy if r["active"])
        return dict(items=len(statuses), active=sum(statuses), inactive=len(statuses)-sum(statuses), models=len(models))

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
        return MaterialBOM(self.connection).costing(code)

    def bom_page(self, *args):
        return MaterialBOM(self.connection).page(*args)

    def change_bom(self, data, actor):
        self.connection.execute("BEGIN IMMEDIATE")
        update_bom(self.connection, data, actor)
        self.audit("维护生产 BOM", "material_bom", data.get("code", ""),
                   f"{data.get('action')} · {data.get('parent', '')}", actor=actor)

    def active_for_weight_backfill(self) -> list[dict]:
        return [r for r in MaterialBOM(self.connection).active_stamps() if r.get("id")]

    def fill_missing_net_weight(self, row: dict, net: str, *, evidence: str, actor: str) -> bool:
        archived = self.connection.execute("""SELECT p.net_weight FROM bom_parts p JOIN bom_links b ON b.child=p.code
            JOIN material_items m ON m.sync_id=b.origin WHERE m.id=? AND b.active=1""", (row["id"],)).fetchone()
        if archived:
            if archived["net_weight"] and archived["net_weight"] != net:
                return False
            self.connection.execute("UPDATE bom_parts SET net_weight=?,updated_at=? WHERE code=?",
                                    (net, now_text(), row["code"]))
            self.audit("补录共享冲压件净重", "material_bom", row["code"], evidence, actor=actor)
            return True
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
        previous = self.get(int(str(data["id"]))) if data.get("id") else None
        if previous and self.connection.execute("""SELECT 1 FROM bom_links WHERE origin=?1 UNION SELECT 1 FROM bom_source_aliases WHERE origin=?1
            UNION SELECT 1 FROM bom_removed_sources WHERE origin=?1""", (previous["sync_id"],)).fetchone():
            raise ValueError("此原始记录已归档，请从 BOM 中编辑共享资料或装配用量。")
        if not previous and self.connection.execute("SELECT 1 FROM bom_parts WHERE code=?", (data.get("code"),)).fetchone():
            raise ValueError("子件已建档，请在母件下添加已有子件。")
        saved = upsert_material_item(self.connection, dict(data), actor=actor, commit=False)
        if data.get("resolve_bom"):
            migrate_rows(self.connection)
        return saved

    def deactivate(self, item_id: int, *, actor: str) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        row = self.get(item_id)
        if row and self.connection.execute("SELECT 1 FROM bom_links WHERE origin=?1 UNION SELECT 1 FROM bom_source_aliases WHERE origin=?1", (row["sync_id"],)).fetchone():
            raise ValueError("此原始记录已归档，请在 BOM 中修改当前母件的关系状态。")
        deactivate_material_item(self.connection, item_id, actor=actor, commit=False)

    def sheet_rows(self) -> dict[str, list[dict]]:
        return MaterialBOM(self.connection).production()

    def preview_import(self, path) -> dict[str, Any]:
        rows = read_material_rows(path)
        seen = {r["sync_id"]: dict(r) for r in self.connection.execute("SELECT * FROM material_items")}
        fresh, conflicts = [], []
        for r in rows:
            old = seen.get(r["sync_id"])
            if old:
                if r["net_weight"] and old["net_weight"] != r["net_weight"]:
                    conflicts.append(f"第 {r['source_row']} 行 {r['model']} / {r['code']} 净重不同")
                continue
            fresh.append(r)
        self.connection.execute("SAVEPOINT bom_import")
        try:
            insert_material_rows(self.connection, fresh)
            migrate_rows(self.connection)
            adopted = {r[0] for r in self.connection.execute("SELECT origin FROM bom_links WHERE origin IS NOT NULL")}
            conflicts.extend(f"第 {r['source_row']} 行 {r['model']} / {r['code'] or '缺少编码'} 资料冲突或重复"
                             for r in fresh if r["sync_id"] not in adopted)
        finally:
            self.connection.execute("ROLLBACK TO bom_import")
            self.connection.execute("RELEASE bom_import")
        added = [r for r in fresh if r["sync_id"] in adopted]
        unchanged = len(rows) - len(added) - len(conflicts)
        signature = [[r["sync_id"], r["net_weight"]] for r in added], conflicts, unchanged
        digest = sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
        return dict(new=added, conflicts=conflicts, unchanged=unchanged, digest=digest)

    def import_data(self, path: Path, *, actor: str, digest: str = "") -> int:
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        preview = self.preview_import(path)
        if digest and digest != preview["digest"]:
            raise ValueError("预览后资料已变化，请重新上传预览。")
        if preview["conflicts"]:
            raise ValueError("数据存在冲突，请核对后重新导入。")
        insert_material_rows(self.connection, preview["new"])
        migrate_rows(self.connection)
        return len(preview["new"])

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
