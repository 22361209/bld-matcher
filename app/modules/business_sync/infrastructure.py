from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from app.database import connect
from app.platform.audit_store import log_event
from app.platform.sync_identity import material_match_key, quote_match_key, stable_sync_id


PACKAGE_SUFFIX = ".tar.gz"
PACKAGE_TYPE = "bld_business_data"
DATASETS = {
    "customers": ("customers", "sync_id", "客户"),
    "products": ("products", "bld_no", "产品目录"),
    "quotes": ("quote_records", "sync_id", "报价记录"),
    "tubes": ("tube_items", "code", "管件资料"),
    "materials": ("material_items", "sync_id", "材料明细"),
}
FIELD_LABELS = {
    "active": "状态", "bld_no": "BLD NO.", "blank_length_text": "毛坯管长度", "borrowed_from": "借用编号",
    "car": "车型", "category": "类别", "code": "编号", "consumption_mm": "消耗长度", "currency": "币种",
    "customer_name": "客户", "customer_product_code": "客户产品编号", "inner_diameter_mm": "内径", "item": "产品名称",
    "length": "长度", "model": "母件编码", "models": "适用车型", "moq": "起订量", "name": "客户名称", "note": "备注",
    "oe_no_1": "OE 号 1", "oe_no_2": "OE 号 2", "outer_diameter_mm": "外径", "part": "零件",
    "pieces": "下料只数", "price": "报价", "price_cny": "价格", "product_model": "产品型号", "product_status": "产品状态",
    "purchase_base": "采购基数", "quote_date": "报价日期", "quote_no": "报价单号", "quoted_by": "报价人", "remark": "备注", "series": "系列",
    "source": "来源", "source_row": "来源行", "source_sheet": "来源工作表", "source_text": "来源内容", "source_type": "来源类型",
    "spec_text": "规格", "tax_price": "含税价", "net_price": "未税价", "thickness": "厚度", "tolerance_mm": "公差",
    "tube_type": "产品名称", "weight_kg": "重量", "width": "宽度",
}
COMPARISON_EXCLUDED_COLUMNS = {"sync_id", "attachment_path", "created_at", "updated_at", "version"}
LOCAL_MEDIA_COLUMNS = {
    # 负责人账号属于设备本地身份，不随客户主数据跨设备覆盖。
    "customers": {"owner_username"},
    "products": {"image_path", "image_path_2", "image_path_3", "image_path_4", "image_path_5", "drawing_path", "drawing_original_name", "drawing_updated_at"},
    # customer_id 是每台设备的本地主键，跨设备同步时按 customer_name 重新解析。
    "quotes": {"attachment_path", "customer_id"},
}


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})") if row["name"] != "id"]


def _changed(key: str, local: sqlite3.Row | None, incoming: dict[str, object], columns: list[str]) -> bool:
    return local is None or any(local[column] != incoming.get(column) for column in columns if column not in LOCAL_MEDIA_COLUMNS.get(key, set()))


def _older(local: sqlite3.Row, incoming: dict[str, object]) -> bool:
    return str(incoming.get("updated_at") or "") < str(local["updated_at"] or "")


def _status(key: str, local: sqlite3.Row | None, incoming: dict[str, object], columns: list[str]) -> str:
    if local is None:
        return "new"
    if not _changed(key, local, incoming, columns):
        return "unchanged"
    if key == "quotes" or _older(local, incoming):
        return "conflict"
    return "updated"


def _unresolved_customers(connection: sqlite3.Connection, payload: dict[str, list[dict[str, object]]]) -> list[str]:
    """报价行里本机 customers 表和数据包 customers 数据集都不存在的客户名。"""

    names = {str(row.get("customer_name") or "").strip() for row in payload.get("quotes", [])}
    names.discard("")
    if not names:
        return []
    local = {str(row["name"]).upper() for row in connection.execute("SELECT name FROM customers").fetchall()}
    incoming = {str(row.get("name") or "").strip().upper() for row in payload.get("customers", [])}
    return sorted(name for name in names if name.upper() not in local and name.upper() not in incoming)


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

    quotes = connection.execute(
        "SELECT id, customer_id, customer_name FROM quote_records ORDER BY id"
    ).fetchall()
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


def _package_digest(package_path: Path) -> str:
    digest = hashlib.sha256()
    with package_path.open("rb") as package:
        while chunk := package.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _state_token(connection: sqlite3.Connection, package_path: Path, datasets: tuple[str, ...]) -> str:
    state: dict[str, list[list[object]]] = {}
    for key in datasets:
        table, identity, _label = DATASETS[key]
        columns = _columns(connection, table)
        rows = connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY {identity}"
        ).fetchall()
        state[key] = [[row[column] for column in columns] for row in rows]
    payload = json.dumps(
        {"package": _package_digest(package_path), "state": state},
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_row(key: str, local_rows: list[sqlite3.Row], incoming: dict[str, object]) -> sqlite3.Row | None:
    key_factory = quote_match_key if key == "quotes" else material_match_key
    candidates = [row for row in local_rows if key_factory(dict(row)) == key_factory(incoming)]
    return candidates[0] if len(candidates) == 1 else None


def _equivalent_without_sync(key: str, local: sqlite3.Row, incoming: dict[str, object], columns: list[str]) -> bool:
    ignored = {"sync_id", "created_at", "updated_at", "version"} | LOCAL_MEDIA_COLUMNS.get(key, set())
    return all(local[column] == incoming.get(column) for column in columns if column not in ignored)


def _incoming_status(
    key: str,
    local: dict[str, sqlite3.Row],
    local_rows: list[sqlite3.Row],
    incoming: dict[str, object],
    columns: list[str],
) -> tuple[str, sqlite3.Row | None, bool]:
    identity = DATASETS[key][1]
    local_row = local.get(str(incoming[identity]))
    if local_row is not None:
        return _status(key, local_row, incoming, columns), local_row, False
    if key not in {"quotes", "materials"}:
        return "new", None, False
    candidate = _candidate_row(key, local_rows, incoming)
    if candidate is None:
        return "new", None, False
    if _equivalent_without_sync(key, candidate, incoming, columns):
        return "updated", candidate, True
    return "conflict", candidate, False


def _preview_label(key: str, incoming: dict[str, object]) -> str:
    if key == "customers":
        return str(incoming.get("name") or "—")
    if key == "materials":
        fields = ("model", "code", "category", "car", "part", "spec_text")
        return " · ".join(str(incoming.get(field) or "—") for field in fields)
    if key == "quotes":
        fields = ("customer_name", "bld_no", "customer_product_code", "quote_date")
        return " · ".join(str(incoming.get(field) or "—") for field in fields)
    return str(incoming.get(DATASETS[key][1]) or "—")


def _display_value(value: object) -> str:
    return "—" if value is None or value == "" else str(value)


def _comparison_fields(key: str, local: sqlite3.Row, incoming: dict[str, object], columns: list[str]) -> list[dict[str, str]]:
    return [
        {"label": FIELD_LABELS.get(column, column), "before": _display_value(local[column]), "after": _display_value(incoming.get(column))}
        for column in columns
        if column not in COMPARISON_EXCLUDED_COLUMNS | LOCAL_MEDIA_COLUMNS.get(key, set()) and local[column] != incoming.get(column)
    ]


def _all_comparison_fields(key: str, local: sqlite3.Row, incoming: dict[str, object], columns: list[str]) -> list[dict[str, str | bool]]:
    return [
        {
            "label": FIELD_LABELS.get(column, column),
            "before": _display_value(local[column]),
            "after": _display_value(incoming.get(column)),
            "changed": local[column] != incoming.get(column),
        }
        for column in columns
        if column not in COMPARISON_EXCLUDED_COLUMNS | LOCAL_MEDIA_COLUMNS.get(key, set())
    ]


class BusinessSyncRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def export(self, *, output_path: Path, selected: tuple[str, ...], actor: str) -> Path:
        payload: dict[str, list[dict[str, object]]] = {}
        with connect(self.database_path) as connection:
            for key in selected:
                table, _identity, _label = DATASETS[key]
                columns = _columns(connection, table)
                rows = [dict(row) for row in connection.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()]
                for row in rows:
                    for column in LOCAL_MEDIA_COLUMNS.get(key, set()):
                        row[column] = ""
                payload[key] = rows
            log_event(connection, "导出业务数据包", "business_sync", output_path.name, f"包含：{'、'.join(DATASETS[key][2] for key in selected)}", actor=actor)
            connection.commit()
        manifest = {"package_type": PACKAGE_TYPE, "version": 1, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "datasets": list(selected)}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with tempfile.TemporaryDirectory(prefix="bld-business-sync-") as directory:
                root = Path(directory)
                (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                (root / "data.json").write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
                with tarfile.open(temporary, "w:gz") as archive:
                    archive.add(root / "manifest.json", arcname="manifest.json")
                    archive.add(root / "data.json", arcname="data.json")
            os.replace(temporary, output_path)
        finally:
            temporary.unlink(missing_ok=True)
        return output_path

    @staticmethod
    def read(package_path: Path) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
        try:
            with tarfile.open(package_path, "r:gz") as archive:
                raw_members = archive.getmembers()
                members = {member.name: member for member in raw_members}
                if len(raw_members) != 2 or len(members) != 2 or set(members) != {"manifest.json", "data.json"} or any(not member.isfile() or member.issym() or member.islnk() or member.size > 64 * 1024 * 1024 for member in members.values()):
                    raise ValueError("业务数据包格式或文件大小无效。")
                manifest_file = archive.extractfile(members["manifest.json"])
                data_file = archive.extractfile(members["data.json"])
                if manifest_file is None or data_file is None:
                    raise ValueError("业务数据包内容不完整。")
                manifest = json.loads(manifest_file.read().decode("utf-8"))
                payload = json.loads(data_file.read().decode("utf-8"))
        except (OSError, tarfile.TarError, json.JSONDecodeError) as exc:
            raise ValueError("业务数据包无法读取。") from exc
        if not isinstance(manifest, dict) or manifest.get("package_type") != PACKAGE_TYPE or not isinstance(payload, dict):
            raise ValueError("不是受支持的业务数据包。")
        selected = tuple(key for key in manifest.get("datasets", []) if key in DATASETS)
        if not selected or set(payload) != set(selected) or any(not isinstance(payload.get(key), list) for key in selected):
            raise ValueError("业务数据包缺少可导入的数据集。")
        for key in selected:
            identity = DATASETS[key][1]
            seen: set[str] = set()
            duplicates: list[str] = []
            for row in payload[key]:
                if not isinstance(row, dict) or not str(row.get(identity) or "").strip():
                    raise ValueError(f"{DATASETS[key][2]}包含无效编号。")
                value = str(row[identity])
                if value in seen:
                    duplicates.append(value)
                seen.add(value)
            if duplicates:
                raise ValueError(f"{DATASETS[key][2]}包含重复编号：{'、'.join(duplicates[:10])}")
        return manifest, {key: payload[key] for key in selected}

    def preview(self, package_path: Path) -> dict[str, object]:
        manifest, payload = self.read(package_path)
        summary: dict[str, dict[str, object]] = {}
        with connect(self.database_path) as connection:
            for key, incoming_rows in payload.items():
                table, identity, label = DATASETS[key]
                columns = _columns(connection, table)
                if any(identity not in row or any(column not in row for column in columns) for row in incoming_rows if isinstance(row, dict)):
                    raise ValueError(f"{label}字段与当前系统不一致，请先升级后再导入。")
                local_rows = connection.execute(f"SELECT * FROM {table}").fetchall()
                local = {str(row[identity]): row for row in local_rows}
                counts = {"new": 0, "updated": 0, "conflict": 0, "unchanged": 0}
                rows: list[dict[str, object]] = []
                conflicts: list[dict[str, object]] = []
                for incoming in incoming_rows:
                    if not isinstance(incoming, dict):
                        raise ValueError(f"{label}包含无效记录。")
                    status, local_row, _adopt_sync_id = _incoming_status(key, local, local_rows, incoming, columns)
                    counts[status] += 1
                    if status == "conflict":
                        conflicts.append({
                            "key": str(incoming[identity]),
                            "label": _preview_label(key, incoming),
                            "fields": _comparison_fields(key, local_row, incoming, columns) if local_row else [],
                            "all_fields": _all_comparison_fields(key, local_row, incoming, columns) if local_row else [],
                            "local_updated_at": local_row["updated_at"] if local_row else "",
                            "incoming_updated_at": incoming.get("updated_at", ""),
                        })
                    if status != "unchanged":
                        rows.append({"status": status, "key": str(incoming[identity]), "label": _preview_label(key, incoming), "local_updated_at": local_row["updated_at"] if local_row else "", "incoming_updated_at": incoming.get("updated_at", "")})
                summary[key] = {"label": label, "counts": counts, "rows": rows, "conflicts": conflicts}
            token = _state_token(connection, package_path, tuple(payload))
            unresolved_customers = _unresolved_customers(connection, payload)
            customer_options = (
                [str(row["name"]) for row in connection.execute("SELECT name FROM customers ORDER BY name COLLATE NOCASE").fetchall()]
                if unresolved_customers
                else []
            )
        return {
            "manifest": manifest,
            "summary": summary,
            "token": token,
            "unresolved_customers": unresolved_customers,
            "customer_options": customer_options,
        }

    @staticmethod
    def _resolve_quote_customers(connection: sqlite3.Connection, payload: dict[str, list[dict[str, object]]], mappings: dict[str, str | None]) -> None:
        unresolved = _unresolved_customers(connection, payload)
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

    def apply(self, package_path: Path, *, backup_path: Path, actor: str, expected_token: str, selected_conflicts: dict[str, set[str]], customer_mappings: dict[str, str | None] | None = None) -> dict[str, dict[str, int]]:
        _manifest, payload = self.read(package_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup = sqlite3.connect(backup_path)
        try:
            with connect(self.database_path) as source:
                source.backup(backup)
            backup.commit()
        finally:
            backup.close()
        result: dict[str, dict[str, int]] = {}
        connection = connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if _state_token(connection, package_path, tuple(payload)) != expected_token:
                raise ValueError("预览后数据包或本机数据已变化，请重新上传预览。")
            preexisting_quote_ids = {
                int(row["id"])
                for row in connection.execute("SELECT id FROM quote_records").fetchall()
            }
            imported_quote_ids: set[int] = set()
            self._resolve_quote_customers(connection, payload, customer_mappings or {})
            for key, incoming_rows in payload.items():
                table, identity, _label = DATASETS[key]
                columns = _columns(connection, table)
                write_columns = [column for column in columns if column not in LOCAL_MEDIA_COLUMNS.get(key, set())]
                insert_sql = ", ".join(write_columns)
                placeholders = ", ".join("?" for _ in write_columns)
                updates = ", ".join(f"{column}=excluded.{column}" for column in write_columns if column != identity)
                local_rows = connection.execute(f"SELECT * FROM {table}").fetchall()
                local = {str(row[identity]): row for row in local_rows}
                counts = {"new": 0, "updated": 0, "conflict": 0, "unchanged": 0}
                for incoming in incoming_rows:
                    status, local_row, adopt_sync_id = _incoming_status(key, local, local_rows, incoming, columns)
                    selected_conflict = status == "conflict" and str(incoming[identity]) in selected_conflicts.get(key, set())
                    if selected_conflict:
                        status = "updated"
                    counts[status] += 1
                    if status in {"unchanged", "conflict"}:
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
                        connection.execute(f"UPDATE {table} SET sync_id = ? WHERE id = ?", (incoming[identity], local_row["id"]))
                        continue
                    connection.execute(
                        f"INSERT INTO {table} ({insert_sql}) VALUES ({placeholders}) "
                        f"ON CONFLICT({identity}) DO UPDATE SET {updates}",
                        _write_values(key, write_columns, incoming, local_row),
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
            if {"customers", "quotes"}.intersection(payload):
                _normalize_quote_customer_links(
                    connection,
                    preexisting_quote_ids=preexisting_quote_ids,
                    imported_quote_ids=imported_quote_ids,
                )
            log_event(connection, "导入业务数据包", "business_sync", package_path.name, "；".join(f"{DATASETS[key][2]}新增 {counts['new']}、更新 {counts['updated']}、冲突 {counts['conflict']}" for key, counts in result.items()), actor=actor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return result
