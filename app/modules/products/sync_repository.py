from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.database import connect
from app.platform.audit_store import log_event

from .sync_domain import ProductDiff, ProductSyncResult


PRODUCT_TABLE = "products"
ConnectionFactory = Callable[[Path], sqlite3.Connection]


def _product_columns(connection: sqlite3.Connection, schema: str = "main") -> list[str]:
    if schema not in {"main", "incoming"}:
        raise ValueError("Unsupported product schema.")
    return [str(row["name"]) for row in connection.execute(f"PRAGMA {schema}.table_info({PRODUCT_TABLE})")]


def _parse_updated_at(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _row_changed(local_row: sqlite3.Row | None, incoming_row: sqlite3.Row, columns: list[str]) -> bool:
    return local_row is None or any(local_row[column] != incoming_row[column] for column in columns if column != "id")


def _incoming_is_older(local_row: sqlite3.Row | None, incoming_row: sqlite3.Row) -> bool:
    if local_row is None:
        return False
    local_updated = _parse_updated_at(local_row["updated_at"])
    incoming_updated = _parse_updated_at(incoming_row["updated_at"])
    if local_updated and incoming_updated is None:
        return True
    return bool(local_updated and incoming_updated and incoming_updated < local_updated)


class SQLiteProductSyncRepository:
    def __init__(
        self,
        database_path: Path,
        *,
        connection_factory: ConnectionFactory = connect,
    ) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory

    def export_products_database(self, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(target_path)
        try:
            with self.connection_factory(self.database_path) as source:
                target.row_factory = sqlite3.Row
                schema_row = source.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (PRODUCT_TABLE,),
                ).fetchone()
                if not schema_row or not schema_row["sql"]:
                    raise RuntimeError("当前数据库缺少 products 表。")
                target.execute(str(schema_row["sql"]))
                columns = _product_columns(source)
                column_sql = ", ".join(columns)
                placeholders = ", ".join("?" for _ in columns)
                rows = source.execute(
                    f"SELECT {column_sql} FROM products ORDER BY bld_no COLLATE BLD_NATURAL"
                ).fetchall()
                target.executemany(
                    f"INSERT INTO products ({column_sql}) VALUES ({placeholders})",
                    ([row[column] for column in columns] for row in rows),
                )
                target.commit()
        finally:
            target.close()

    def diff(self, package_database: Path, *, limit: int = 50) -> ProductDiff:
        connection = self.connection_factory(self.database_path)
        attached = False
        try:
            connection.execute("ATTACH DATABASE ? AS incoming", (str(package_database),))
            attached = True
            local_columns = _product_columns(connection, "main")
            incoming_columns = _product_columns(connection, "incoming")
            if set(local_columns) != set(incoming_columns):
                raise ValueError("数据包 products 表结构与当前系统不一致，请先升级程序后再导入。")
            column_sql = ", ".join(local_columns)
            incoming_rows = connection.execute(
                f"SELECT {column_sql} FROM incoming.products ORDER BY bld_no COLLATE BLD_NATURAL"
            ).fetchall()
            local_by_bld = {
                str(row["bld_no"]): row for row in connection.execute("SELECT * FROM main.products").fetchall()
            }
            rows: list[dict[str, object]] = []
            new_count = updated_count = conflict_count = unchanged_count = 0
            for row in incoming_rows:
                local_row = local_by_bld.get(str(row["bld_no"]))
                if local_row is None:
                    new_count += 1
                    status = "new"
                elif _row_changed(local_row, row, local_columns) and _incoming_is_older(local_row, row):
                    conflict_count += 1
                    status = "conflict"
                elif _row_changed(local_row, row, local_columns):
                    updated_count += 1
                    status = "updated"
                else:
                    unchanged_count += 1
                    status = "unchanged"
                if status != "unchanged" and len(rows) < limit:
                    rows.append(
                        {
                            "status": status,
                            "bld_no": row["bld_no"],
                            "local_updated_at": local_row["updated_at"] if local_row else "",
                            "incoming_updated_at": row["updated_at"],
                            "local_price": local_row["price_cny"] if local_row else None,
                            "incoming_price": row["price_cny"],
                        }
                    )
            local_only_rows = connection.execute(
                """
                SELECT * FROM main.products
                WHERE bld_no NOT IN (SELECT bld_no FROM incoming.products)
                ORDER BY bld_no COLLATE BLD_NATURAL
                """
            ).fetchall()
            for local_row in local_only_rows:
                if len(rows) >= limit:
                    break
                rows.append(
                    {
                        "status": "local_only",
                        "bld_no": local_row["bld_no"],
                        "local_updated_at": local_row["updated_at"],
                        "incoming_updated_at": "",
                        "local_price": local_row["price_cny"],
                        "incoming_price": None,
                    }
                )
            return ProductDiff(
                new_count=new_count,
                updated_count=updated_count,
                conflict_count=conflict_count,
                unchanged_count=unchanged_count,
                local_only_count=len(local_only_rows),
                rows=rows,
            )
        finally:
            if attached:
                try:
                    connection.execute("DETACH DATABASE incoming")
                except sqlite3.Error:
                    pass
            connection.close()

    def apply(
        self,
        package_database: Path,
        *,
        deactivate_local_only: bool,
        actor: str,
    ) -> ProductSyncResult:
        connection = self.connection_factory(self.database_path)
        attached = False
        try:
            connection.execute("ATTACH DATABASE ? AS incoming", (str(package_database),))
            attached = True
            columns = _product_columns(connection, "main")
            incoming_columns = _product_columns(connection, "incoming")
            if set(columns) != set(incoming_columns):
                raise ValueError("数据包 products 表结构与当前系统不一致，请先升级程序后再导入。")
            insert_columns = [column for column in columns if column != "id"]
            column_sql = ", ".join(insert_columns)
            placeholders = ", ".join("?" for _ in insert_columns)
            assignments = ", ".join(
                f"{column} = excluded.{column}" for column in insert_columns if column != "bld_no"
            )
            new_count = updated_count = conflict_count = unchanged_count = deactivated_count = 0
            rows = connection.execute(
                f"SELECT {', '.join(columns)} FROM incoming.products ORDER BY bld_no COLLATE BLD_NATURAL"
            ).fetchall()
            with connection:
                for row in rows:
                    local_row = connection.execute(
                        "SELECT * FROM main.products WHERE bld_no = ?",
                        (row["bld_no"],),
                    ).fetchone()
                    if local_row is None:
                        new_count += 1
                    elif _row_changed(local_row, row, columns) and _incoming_is_older(local_row, row):
                        conflict_count += 1
                        continue
                    elif _row_changed(local_row, row, columns):
                        updated_count += 1
                    else:
                        unchanged_count += 1
                        continue
                    connection.execute(
                        f"""
                        INSERT INTO main.products ({column_sql}) VALUES ({placeholders})
                        ON CONFLICT(bld_no) DO UPDATE SET {assignments}
                        """,
                        [row[column] for column in insert_columns],
                    )
                if deactivate_local_only:
                    cursor = connection.execute(
                        """
                        UPDATE main.products SET active = 0, updated_at = ?
                        WHERE active = 1 AND bld_no NOT IN (SELECT bld_no FROM incoming.products)
                        """,
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
                    )
                    deactivated_count = int(cursor.rowcount)
                log_event(
                    connection,
                    "导入产品数据包",
                    "product_sync",
                    "products.sqlite3",
                    f"新增 {new_count} 条，更新 {updated_count} 条，跳过无变化 {unchanged_count} 条，跳过包内旧数据 {conflict_count} 条，停用本机独有 {deactivated_count} 条；保留当前系统账号、API Key 和日志。",
                    actor=actor,
                )
            return ProductSyncResult(
                new_count=new_count,
                updated_count=updated_count,
                conflict_count=conflict_count,
                unchanged_count=unchanged_count,
                deactivated_count=deactivated_count,
            )
        finally:
            if attached:
                try:
                    connection.execute("DETACH DATABASE incoming")
                except sqlite3.Error:
                    pass
            connection.close()

    def backup(self, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(target_path)
        try:
            with self.connection_factory(self.database_path) as source:
                source.backup(target)
            target.commit()
        finally:
            target.close()

    def audit(self, action: str, target_key: str, detail: str, *, actor: str) -> None:
        with self.connection_factory(self.database_path) as connection:
            log_event(connection, action, "product_sync", target_key, detail, actor=actor)
            connection.commit()
