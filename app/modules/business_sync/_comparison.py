from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.modules.costing.business_sync import costing_state
from app.modules.materials.domain import material_grade_for_code
from app.modules.materials.weights import validated_net_weight
from app.modules.products.brand_normalization import canonicalize_brands
from app.modules.products.catalog_language import catalog_values
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


def state_token(connection: sqlite3.Connection, package_path: Path, datasets: tuple[str, ...], *, material_sources: set[str],
                development_file_dir: Path | None = None, development_rows: list[dict] | None = None) -> str:
    state: dict[str, object] = {}
    if "materials" in datasets:
        state["stamping_source_ids"] = sorted(material_sources)
    for key in datasets:
        if key == "costing":
            state[key] = costing_state(connection)
            continue
        if key == "development":
            from ._development import development_state
            state[key] = development_state(connection, development_rows or [], development_file_dir)
            continue
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


MATERIAL_IDENTITY_CONFLICT = "本机成本单正在使用这条材料取重，须保留本机材料的同步编号。"


@dataclass(frozen=True)
class MaterialIdentityMatches:
    by_incoming_id: dict[str, sqlite3.Row]
    protected: set[str]


def legacy_material_matches(local: dict[str, sqlite3.Row], incoming_rows: list[dict], material_sources: set[str]) -> MaterialIdentityMatches:
    """Reserve exact identities first, then match unmatched rows one-to-one using their old business key."""
    incoming_ids = {str(row["sync_id"]) for row in incoming_rows}
    available = defaultdict(list)
    for identity, row in local.items():
        if identity not in incoming_ids:
            available[material_match_key(dict(row))].append(row)
    unmatched = defaultdict(list)
    for incoming in incoming_rows:
        identity = str(incoming["sync_id"])
        if identity not in local:
            unmatched[material_match_key(incoming)].append(identity)
    matches = {identities[0]: available[key][0] for key, identities in unmatched.items()
               if len(identities) == 1 and len(available[key]) == 1}
    protected = {incoming_id for incoming_id, row in matches.items() if row["sync_id"] in material_sources}
    return MaterialIdentityMatches(matches, protected)


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
    material_matches: MaterialIdentityMatches | None,
) -> tuple[str, sqlite3.Row | None, bool]:
    identity = DATASETS[key][1]
    local_row = local.get(str(incoming[identity]))
    if key == "products":
        incoming.update(catalog_values(incoming, dict(local_row) if local_row else {}))
    candidate = None
    if local_row is None and key == "quotes":
        match = quote_match_key(incoming)
        candidates = [row for row in local_rows if quote_match_key(dict(row)) == match]
        candidate = candidates[0] if len(candidates) == 1 else None
    if material_matches is not None:
        incoming_id = str(incoming[identity])
        candidate = material_matches.by_incoming_id.get(incoming_id)
        prior = local_row or candidate
        incoming["material_grade"] = material_grade_for_code(incoming["code"])
        incoming["net_weight"] = validated_net_weight(
            incoming, incoming.get("net_weight", prior["net_weight"] if prior is not None else None)
        )
        if incoming_id in material_matches.protected:
            return "conflict", candidate, False
        # A legacy ID adoption can retain newer local timestamps without creating a fieldless conflict.
        if (local_row is not None and str(incoming.get("updated_at") or "") <= str(local_row["updated_at"] or "")
                and equivalent_without_sync(key, local_row, incoming, record_columns)):
            return "unchanged", local_row, False
    if local_row is not None:
        return status(key, local_row, incoming, record_columns), local_row, False
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
