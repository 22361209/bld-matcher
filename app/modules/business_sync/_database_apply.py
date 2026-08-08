from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.database import connect
from app.modules.products.option_values import register_product_option_values
from app.platform.audit_store import log_event
from app.platform.sync_identity import stable_sync_id

from ._comparison import columns, incoming_status, normalized_incoming, state_token, unresolved_customers
from ._package_archive import PackageReader
from ._schema import DATASETS, LOCAL_MEDIA_COLUMNS, MEDIA_DATASETS


IncomingNormalizer = Callable[[str, object], dict[str, object]]
MediaCopy = Callable[
    [Path, dict[str, object], dict[str, bool], Path, list[tuple[Path, Path | None]]],
    None,
]
MediaRestore = Callable[[list[tuple[Path, Path | None]]], None]
QuoteCustomerResolver = Callable[
    [sqlite3.Connection, dict[str, list[dict[str, object]]], dict[str, str | None]],
    None,
]


def _customer_name_key(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _normalize_quote_customer_links(
    connection: sqlite3.Connection,
    *,
    preexisting_quote_ids: set[int],
    imported_quote_ids: set[int],
) -> None:
    """Keep quote customer names aligned with device-local customer identities."""

    customers = connection.execute("SELECT id, name FROM customers ORDER BY id").fetchall()
    customers_by_id = {int(row["id"]): str(row["name"]) for row in customers}
    customers_by_name: dict[str, list[tuple[int, str]]] = {}
    for row in customers:
        customer_id = int(row["id"])
        customer_name = str(row["name"])
        customers_by_name.setdefault(_customer_name_key(customer_name), []).append((customer_id, customer_name))

    quotes = connection.execute("SELECT id, customer_id, customer_name FROM quote_records ORDER BY id").fetchall()
    for row in quotes:
        quote_id = int(row["id"])
        raw_customer_id = row["customer_id"]
        try:
            local_customer_id = int(raw_customer_id) if raw_customer_id is not None else None
        except (TypeError, ValueError):
            local_customer_id = None

        if quote_id in imported_quote_ids:
            matches = customers_by_name.get(_customer_name_key(row["customer_name"]), [])
            if len(matches) != 1:
                raise ValueError(f"报价客户 {row['customer_name']} 无法映射到本机客户。")
            target_customer_id, target_customer_name = matches[0]
        else:
            canonical_name = customers_by_id.get(local_customer_id) if local_customer_id is not None else None
            if canonical_name is not None:
                target_customer_id = local_customer_id
                target_customer_name = canonical_name
            else:
                matches = customers_by_name.get(_customer_name_key(row["customer_name"]), [])
                if len(matches) != 1:
                    if raw_customer_id is not None:
                        if quote_id in preexisting_quote_ids:
                            connection.execute(
                                """
                                UPDATE quote_records
                                SET customer_id = NULL, version = version + 1,
                                    updated_at = datetime('now','localtime')
                                WHERE id = ?
                                """,
                                (quote_id,),
                            )
                        else:
                            connection.execute(
                                "UPDATE quote_records SET customer_id = NULL WHERE id = ?",
                                (quote_id,),
                            )
                    continue
                target_customer_id, target_customer_name = matches[0]

        if raw_customer_id != target_customer_id or row["customer_name"] != target_customer_name:
            if quote_id in imported_quote_ids:
                connection.execute(
                    "UPDATE quote_records SET customer_id = ?, customer_name = ? WHERE id = ?",
                    (target_customer_id, target_customer_name, quote_id),
                )
            elif quote_id in preexisting_quote_ids:
                connection.execute(
                    """
                    UPDATE quote_records
                    SET customer_id = ?, customer_name = ?, version = version + 1,
                        updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (target_customer_id, target_customer_name, quote_id),
                )
            else:
                connection.execute(
                    "UPDATE quote_records SET customer_id = ?, customer_name = ? WHERE id = ?",
                    (target_customer_id, target_customer_name, quote_id),
                )


def _write_values(
    key: str,
    write_columns: list[str],
    incoming: dict[str, object],
    local_row: sqlite3.Row | None,
) -> list[object]:
    values = [incoming[column] for column in write_columns]
    if key == "quotes" and local_row is not None and "version" in write_columns:
        values[write_columns.index("version")] = int(local_row["version"] or 0) + 1
    return values


def resolve_quote_customers(
    connection: sqlite3.Connection,
    payload: dict[str, list[dict[str, object]]],
    mappings: dict[str, str | None],
) -> None:
    unresolved = unresolved_customers(connection, payload)
    if not unresolved:
        return
    missing = [name for name in unresolved if name not in mappings]
    if missing:
        raise ValueError("报价包含本机未登记的客户，请为每个客户选择新建或映射：" + "、".join(missing[:10]))
    for name in unresolved:
        target = mappings.get(name)
        if target is None:
            connection.execute(
                "INSERT OR IGNORE INTO customers (name, sync_id) VALUES (?, ?)",
                (name, stable_sync_id("customer", name.upper(), 1)),
            )
            continue
        local = connection.execute(
            "SELECT name FROM customers WHERE name = ? COLLATE NOCASE",
            (target,),
        ).fetchone()
        if local is None:
            raise ValueError(f"映射目标客户 {target} 不存在，请重新上传预览。")
        canonical = str(local["name"])
        for row in payload["quotes"]:
            if str(row.get("customer_name") or "").strip() == name:
                row["customer_name"] = canonical


def apply_package(
    database_path: Path,
    package_path: Path,
    *,
    backup_path: Path,
    actor: str,
    expected_token: str,
    selected_conflicts: dict[str, set[str]],
    customer_mappings: dict[str, str | None],
    include_drawings: bool,
    include_images: bool,
    include_material_drawings: bool,
    deactivate_local_only: bool,
    read_package_fn: PackageReader,
    normalized_incoming_fn: IncomingNormalizer = normalized_incoming,
    media_copy_fn: MediaCopy,
    media_restore_fn: MediaRestore,
    resolve_quote_customers_fn: QuoteCustomerResolver = resolve_quote_customers,
) -> dict[str, dict[str, int]]:
    manifest, payload = read_package_fn(package_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup = sqlite3.connect(backup_path)
    try:
        with connect(database_path) as source:
            source.backup(backup)
        backup.commit()
    finally:
        backup.close()
    result: dict[str, dict[str, int]] = {}
    media_changes: list[tuple[Path, Path | None]] = []
    media_backup_root = backup_path.with_name(f"{backup_path.name}.media")
    connection = connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if state_token(connection, package_path, tuple(payload)) != expected_token:
            raise ValueError("预览后数据包或本机数据已变化，请重新上传预览。")
        media_requests = {
            "drawings": include_drawings,
            "product_images": include_images,
            "material_drawings": include_material_drawings,
        }
        requested_media = {
            key: requested
            for key, requested in media_requests.items()
            if MEDIA_DATASETS[key] in payload
        }
        media_copy_fn(package_path, manifest, requested_media, media_backup_root, media_changes)
        preexisting_quote_ids = {
            int(row["id"])
            for row in connection.execute("SELECT id FROM quote_records").fetchall()
        }
        imported_quote_ids: set[int] = set()
        resolve_quote_customers_fn(connection, payload, customer_mappings)
        for key, incoming_rows in payload.items():
            table, identity, _label = DATASETS[key]
            record_columns = columns(connection, table)
            write_columns = [
                column for column in record_columns if column not in LOCAL_MEDIA_COLUMNS.get(key, set())
            ]
            insert_sql = ", ".join(write_columns)
            placeholders = ", ".join("?" for _ in write_columns)
            updates = ", ".join(f"{column}=excluded.{column}" for column in write_columns if column != identity)
            local_rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            local = {str(row[identity]): row for row in local_rows}
            counts = {"new": 0, "updated": 0, "conflict": 0, "unchanged": 0}
            for raw_incoming in incoming_rows:
                incoming = normalized_incoming_fn(key, raw_incoming)
                row_status, local_row, adopt_sync_id = incoming_status(
                    key,
                    local,
                    local_rows,
                    incoming,
                    record_columns,
                )
                selected_conflict = (
                    row_status == "conflict"
                    and str(incoming[identity]) in selected_conflicts.get(key, set())
                )
                if selected_conflict:
                    row_status = "updated"
                counts[row_status] += 1
                if row_status in {"unchanged", "conflict"}:
                    continue
                if selected_conflict and local_row is not None and str(local_row[identity]) != str(incoming[identity]):
                    assignments = ", ".join(f"{column} = ?" for column in write_columns)
                    connection.execute(
                        f"UPDATE {table} SET {assignments} WHERE id = ?",
                        _write_values(key, write_columns, incoming, local_row) + [local_row["id"]],
                    )
                    if key == "quotes":
                        imported_quote_ids.add(int(local_row["id"]))
                    continue
                if adopt_sync_id and local_row is not None:
                    connection.execute(
                        f"UPDATE {table} SET sync_id = ? WHERE id = ?",
                        (incoming[identity], local_row["id"]),
                    )
                    continue
                connection.execute(
                    f"INSERT INTO {table} ({insert_sql}) VALUES ({placeholders}) "
                    f"ON CONFLICT({identity}) DO UPDATE SET {updates}",
                    _write_values(key, write_columns, incoming, local_row),
                )
                if key == "products":
                    register_product_option_values(
                        connection,
                        series=str(incoming.get("series") or ""),
                        item=str(incoming.get("item") or ""),
                        product_status=str(incoming.get("product_status") or ""),
                    )
                if key == "quotes":
                    imported = connection.execute(
                        f"SELECT id FROM {table} WHERE {identity} = ?",
                        (incoming[identity],),
                    ).fetchone()
                    if imported is None:
                        raise RuntimeError("Imported quote could not be reloaded.")
                    imported_quote_ids.add(int(imported["id"]))
            result[key] = counts
        if deactivate_local_only and "products" in payload:
            incoming_bld = {str(row["bld_no"]) for row in payload["products"]}
            placeholders = ", ".join("?" for _ in incoming_bld) or "''"
            cursor = connection.execute(
                f"UPDATE products SET active = 0, updated_at = ? WHERE active = 1 AND bld_no NOT IN ({placeholders})",
                [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *sorted(incoming_bld)],
            )
            result["products"]["deactivated"] = int(cursor.rowcount)
        if {"customers", "quotes"}.intersection(payload):
            _normalize_quote_customer_links(
                connection,
                preexisting_quote_ids=preexisting_quote_ids,
                imported_quote_ids=imported_quote_ids,
            )
        log_event(
            connection,
            "导入业务数据包",
            "business_sync",
            package_path.name,
            "；".join(
                f"{DATASETS[key][2]}新增 {counts['new']}、更新 {counts['updated']}、冲突 {counts['conflict']}"
                for key, counts in result.items()
            ),
            actor=actor,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        media_restore_fn(media_changes)
        raise
    finally:
        connection.close()
    return result
