from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.database import connect

from ._comparison import (
    all_comparison_fields,
    columns,
    comparison_fields,
    incoming_status,
    media_summary,
    normalized_incoming,
    preview_label,
    state_token,
    syncable_incoming_rows,
    unresolved_customers,
)
from ._package_archive import PackageReader
from ._schema import DATASETS


IncomingNormalizer = Callable[[str, object], dict[str, object]]
MediaSummarizer = Callable[[dict[str, object]], dict[str, object]]


def preview_package(
    database_path: Path,
    package_path: Path,
    *,
    read_package_fn: PackageReader,
    normalized_incoming_fn: IncomingNormalizer = normalized_incoming,
    media_summary_fn: MediaSummarizer = media_summary,
) -> dict[str, object]:
    manifest, payload = read_package_fn(package_path)
    summary: dict[str, dict[str, object]] = {}
    with connect(database_path) as connection:
        for key, incoming_rows in payload.items():
            table, identity, label = DATASETS[key]
            syncable_rows, ignored_inactive = syncable_incoming_rows(key, incoming_rows)
            record_columns = columns(connection, table)
            if any(
                identity not in row or any(column not in row for column in record_columns)
                for row in incoming_rows
                if isinstance(row, dict)
            ):
                raise ValueError(f"{label}字段与当前系统不一致，请先升级后再导入。")
            local_rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            local = {str(row[identity]): row for row in local_rows}
            counts = {
                "new": 0,
                "updated": 0,
                "conflict": 0,
                "unchanged": 0,
                "ignored_inactive": ignored_inactive,
            }
            rows: list[dict[str, object]] = []
            conflicts: list[dict[str, object]] = []
            for raw_incoming in syncable_rows:
                incoming = normalized_incoming_fn(key, raw_incoming)
                if not isinstance(incoming, dict):
                    raise ValueError(f"{label}包含无效记录。")
                row_status, local_row, _adopt_sync_id = incoming_status(
                    key,
                    local,
                    local_rows,
                    incoming,
                    record_columns,
                )
                counts[row_status] += 1
                if row_status == "conflict":
                    conflicts.append(
                        {
                            "key": str(incoming[identity]),
                            "label": preview_label(key, incoming),
                            "fields": comparison_fields(key, local_row, incoming, record_columns) if local_row else [],
                            "all_fields": (
                                all_comparison_fields(key, local_row, incoming, record_columns) if local_row else []
                            ),
                            "local_updated_at": local_row["updated_at"] if local_row else "",
                            "incoming_updated_at": incoming.get("updated_at", ""),
                        }
                    )
                if row_status != "unchanged":
                    rows.append(
                        {
                            "status": row_status,
                            "key": str(incoming[identity]),
                            "label": preview_label(key, incoming),
                            "local_updated_at": local_row["updated_at"] if local_row else "",
                            "incoming_updated_at": incoming.get("updated_at", ""),
                        }
                    )
            if key == "products":
                incoming_ids = {str(row[identity]) for row in syncable_rows}
                counts["local_only"] = sum(
                    1
                    for row in local_rows
                    if bool(row["active"]) and str(row[identity]) not in incoming_ids
                )
            summary[key] = {"label": label, "counts": counts, "rows": rows, "conflicts": conflicts}
        token = state_token(connection, package_path, tuple(payload))
        missing_customers = unresolved_customers(connection, payload)
        customer_options = (
            [
                str(row["name"])
                for row in connection.execute("SELECT name FROM customers ORDER BY name COLLATE NOCASE").fetchall()
            ]
            if missing_customers
            else []
        )
    return {
        "manifest": manifest,
        "summary": summary,
        "token": token,
        "unresolved_customers": missing_customers,
        "customer_options": customer_options,
        "media": media_summary_fn(manifest),
    }
