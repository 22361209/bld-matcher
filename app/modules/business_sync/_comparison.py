from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from app.modules.products.brand_normalization import canonicalize_brands
from app.platform.sync_identity import material_match_key, quote_match_key

from ._schema import COMPARISON_EXCLUDED_COLUMNS, DATASETS, FIELD_LABELS, LOCAL_MEDIA_COLUMNS, MEDIA_DIRECTORIES


def columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})") if row["name"] != "id"]


def changed(
    key: str,
    local: sqlite3.Row | None,
    incoming: dict[str, object],
    record_columns: list[str],
) -> bool:
    return local is None or any(
        local[column] != incoming.get(column)
        for column in record_columns
        if column not in LOCAL_MEDIA_COLUMNS.get(key, set())
    )


def older(local: sqlite3.Row, incoming: dict[str, object]) -> bool:
    return str(incoming.get("updated_at") or "") < str(local["updated_at"] or "")


def status(
    key: str,
    local: sqlite3.Row | None,
    incoming: dict[str, object],
    record_columns: list[str],
) -> str:
    if local is None:
        return "new"
    if not changed(key, local, incoming, record_columns):
        return "unchanged"
    if key == "quotes" or older(local, incoming):
        return "conflict"
    return "updated"


def unresolved_customers(
    connection: sqlite3.Connection,
    payload: dict[str, list[dict[str, object]]],
) -> list[str]:
    """报价行里本机 customers 表和数据包 customers 数据集都不存在的客户名。"""

    names = {str(row.get("customer_name") or "").strip() for row in payload.get("quotes", [])}
    names.discard("")
    if not names:
        return []
    local = {str(row["name"]).upper() for row in connection.execute("SELECT name FROM customers").fetchall()}
    incoming = {str(row.get("name") or "").strip().upper() for row in payload.get("customers", [])}
    return sorted(name for name in names if name.upper() not in local and name.upper() not in incoming)


def package_digest(package_path: Path) -> str:
    digest = hashlib.sha256()
    with package_path.open("rb") as package:
        while chunk := package.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def state_token(connection: sqlite3.Connection, package_path: Path, datasets: tuple[str, ...]) -> str:
    state: dict[str, list[list[object]]] = {}
    for key in datasets:
        table, identity, _label = DATASETS[key]
        record_columns = columns(connection, table)
        rows = connection.execute(
            f"SELECT {', '.join(record_columns)} FROM {table} ORDER BY {identity}"
        ).fetchall()
        state[key] = [[row[column] for column in record_columns] for row in rows]
    payload = json.dumps(
        {"package": package_digest(package_path), "state": state},
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_row(key: str, local_rows: list[sqlite3.Row], incoming: dict[str, object]) -> sqlite3.Row | None:
    key_factory = quote_match_key if key == "quotes" else material_match_key
    candidates = [row for row in local_rows if key_factory(dict(row)) == key_factory(incoming)]
    return candidates[0] if len(candidates) == 1 else None


def equivalent_without_sync(
    key: str,
    local: sqlite3.Row,
    incoming: dict[str, object],
    record_columns: list[str],
) -> bool:
    ignored = {"sync_id", "created_at", "updated_at", "version"} | LOCAL_MEDIA_COLUMNS.get(key, set())
    return all(local[column] == incoming.get(column) for column in record_columns if column not in ignored)


def incoming_status(
    key: str,
    local: dict[str, sqlite3.Row],
    local_rows: list[sqlite3.Row],
    incoming: dict[str, object],
    record_columns: list[str],
) -> tuple[str, sqlite3.Row | None, bool]:
    identity = DATASETS[key][1]
    local_row = local.get(str(incoming[identity]))
    if local_row is not None:
        return status(key, local_row, incoming, record_columns), local_row, False
    if key not in {"quotes", "materials"}:
        return "new", None, False
    candidate = candidate_row(key, local_rows, incoming)
    if candidate is None:
        return "new", None, False
    if equivalent_without_sync(key, candidate, incoming, record_columns):
        return "updated", candidate, True
    return "conflict", candidate, False


def preview_label(key: str, incoming: dict[str, object]) -> str:
    if key == "customers":
        return str(incoming.get("name") or "—")
    if key == "materials":
        fields = ("model", "code", "category", "car", "part", "spec_text")
        return " · ".join(str(incoming.get(field) or "—") for field in fields)
    if key == "quotes":
        fields = ("customer_name", "bld_no", "customer_product_code", "quote_date")
        return " · ".join(str(incoming.get(field) or "—") for field in fields)
    return str(incoming.get(DATASETS[key][1]) or "—")


def display_value(value: object) -> str:
    return "—" if value is None or value == "" else str(value)


def comparison_fields(
    key: str,
    local: sqlite3.Row,
    incoming: dict[str, object],
    record_columns: list[str],
) -> list[dict[str, str]]:
    return [
        {
            "label": FIELD_LABELS.get(column, column),
            "before": display_value(local[column]),
            "after": display_value(incoming.get(column)),
        }
        for column in record_columns
        if column not in COMPARISON_EXCLUDED_COLUMNS | LOCAL_MEDIA_COLUMNS.get(key, set())
        and local[column] != incoming.get(column)
    ]


def all_comparison_fields(
    key: str,
    local: sqlite3.Row,
    incoming: dict[str, object],
    record_columns: list[str],
) -> list[dict[str, str | bool]]:
    return [
        {
            "label": FIELD_LABELS.get(column, column),
            "before": display_value(local[column]),
            "after": display_value(incoming.get(column)),
            "changed": local[column] != incoming.get(column),
        }
        for column in record_columns
        if column not in COMPARISON_EXCLUDED_COLUMNS | LOCAL_MEDIA_COLUMNS.get(key, set())
    ]


def normalized_incoming(key: str, incoming: object) -> dict[str, object]:
    if not isinstance(incoming, dict):
        raise ValueError(f"{DATASETS[key][2]}包含无效记录。")
    normalized = dict(incoming)
    if key == "products":
        normalized["series"] = canonicalize_brands(normalized.get("series"))
    return normalized


def syncable_incoming_rows(
    key: str,
    incoming_rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    """Exclude disabled products from cross-device business synchronization."""

    if key != "products":
        return incoming_rows, 0
    syncable = [row for row in incoming_rows if row.get("active") not in (0, False, "0")]
    return syncable, len(incoming_rows) - len(syncable)


def media_summary(manifest: dict[str, object]) -> dict[str, object]:
    media = manifest.get("media", {})
    if not isinstance(media, dict):
        media = {}
    files = media.get("files", {})
    if not isinstance(files, dict):
        files = {}
    normalized_files: dict[str, int] = {}
    for key in MEDIA_DIRECTORIES:
        value = files.get(key, 0)
        normalized_files[key] = value if type(value) is int and value >= 0 else 0
    return {
        "drawings": media.get("drawings") is True,
        "product_images": media.get("product_images") is True,
        "material_drawings": media.get("material_drawings") is True,
        "files": normalized_files,
    }
