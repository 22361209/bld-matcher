from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil
import tempfile

from app.database import connect

from app.modules.products.catalog_language import CATALOG_FIELDS

from ._comparison import (
    all_comparison_fields,
    package_digest,
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
from ._development import preview_aggregates


IncomingNormalizer = Callable[[str, object], dict[str, object]]
MediaSummarizer = Callable[[dict[str, object]], dict[str, object]]


def _preview_package(
    database_path: Path,
    package_path: Path,
    *,
    read_package_fn: PackageReader,
    normalized_incoming_fn: IncomingNormalizer = normalized_incoming,
    media_summary_fn: MediaSummarizer = media_summary,
    development_file_dir: Path | None = None,
    allow_development: bool = True,
) -> dict[str, object]:
    manifest, payload = read_package_fn(package_path)
    if "development" in payload and not allow_development:
        raise ValueError("没有开发资料及文件访问权限。")
    summary: dict[str, dict[str, object]] = {}
    with connect(database_path) as connection:
        if "development" in payload:
            connection.execute("BEGIN")
        for key, incoming_rows in payload.items():
            if key == "development":
                summary[key] = preview_aggregates(connection, incoming_rows, development_file_dir)
                continue
            table, identity, label = DATASETS[key]
            syncable_rows, ignored_inactive = syncable_incoming_rows(key, incoming_rows)
            record_columns = columns(connection, table)
            if any(
                identity not in row or any(column not in row for column in record_columns
                                          if key != "products" or column not in CATALOG_FIELDS)
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
        token = state_token(connection, package_path, tuple(payload), development_file_dir=development_file_dir, development_rows=payload.get("development", []))
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


def preview_package(database_path: Path, package_path: Path, *, read_package_fn: PackageReader, **kwargs) -> dict[str, object]:
    manifest, payload = read_package_fn(package_path)
    if "development" not in payload:
        return _preview_package(database_path, package_path, read_package_fn=lambda _path: (manifest, payload), **kwargs)
    with tempfile.TemporaryDirectory(prefix="bld-development-preview-") as directory:
        snapshot = Path(directory) / package_path.name
        shutil.copyfile(package_path, snapshot)
        result = _preview_package(database_path, snapshot, read_package_fn=read_package_fn, **kwargs)
        if package_digest(snapshot) != package_digest(package_path):
            raise ValueError("预览时数据包已变化，请重新上传预览。")
        return result
