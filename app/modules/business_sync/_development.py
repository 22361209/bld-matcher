"""Whole-project comparison and transactional application for sync package v5."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ._development_documents import export_documents, import_documents, validate_documents
from ._development_files import copy_accepted_files, file_snapshot


AGGREGATE_KEYS = ("branches", "tasks", "components", "operations", "suppliers", "sources", "documents", "revisions", "links")


def export_aggregates(connection: sqlite3.Connection) -> list[dict]:
    from app.modules.development.repository import sync_export_aggregates
    result = sync_export_aggregates(connection)
    for aggregate in result:
        aggregate.update(export_documents(connection, aggregate["project"]["id"]))
        aggregate["id"] = aggregate["project"]["id"]
    return result


def validate_aggregate(aggregate: dict, connection: sqlite3.Connection | None = None) -> None:
    from app.modules.development.repository import sync_validate_aggregate as validate_core
    if not isinstance(aggregate, dict) or set(aggregate) != {"id", "project", *AGGREGATE_KEYS}:
        raise ValueError("开发数据包整单结构不完整。")
    if not isinstance(aggregate["project"], dict) or aggregate["id"] != aggregate["project"].get("id"):
        raise ValueError("开发单永久编号不一致。")
    validate_core(aggregate)
    validate_documents(aggregate, connection)


def _comparable(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "version"}


def aggregate_status(local: dict | None, incoming: dict) -> str:
    if local is None:
        return "new"
    if _comparable(local["project"]) != _comparable(incoming["project"]):
        return "conflict"
    for key in AGGREGATE_KEYS:
        local_rows = {row["id"]: row for row in local[key]}
        if any(_comparable(local_rows.get(row["id"], {})) != _comparable(row) for row in incoming[key]):
            return "conflict"
    return "unchanged"


def preview_aggregates(connection: sqlite3.Connection, incoming: list[dict], root: Path | None) -> dict:
    local = {row["id"]: row for row in export_aggregates(connection)}
    counts = dict(new=0, updated=0, conflict=0, unchanged=0, ignored_inactive=0)
    rows, conflicts = [], []
    for aggregate in incoming:
        validate_aggregate(aggregate, connection)
        _validate_local_ids(connection, aggregate)
        status = aggregate_status(local.get(aggregate["id"]), aggregate)
        counts[status] += 1
        if status == "unchanged":
            continue
        project = aggregate["project"]
        label = str(project.get("core_number") or project.get("name") or aggregate["id"])
        row = {"key": aggregate["id"], "label": label, "status": status, "local_updated_at": "", "incoming_updated_at": project.get("updated_at", "")}
        rows.append(row)
        if status == "conflict":
            before = local[aggregate["id"]]
            fields = aggregate_fields(before, aggregate)
            conflicts.append({**row, "fields": fields, "all_fields": fields})
    # Also inspect incoming destinations, including unreferenced on-disk collisions.
    snapshot = file_snapshot(root, [*local.values(), *incoming])
    for aggregate in incoming:
        for row in aggregate["revisions"]:
            actual = snapshot[row["storage_path"]]
            if actual not in {"missing", row["sha256"]}:
                raise ValueError("同一开发修订 ID 对应不同文件内容，拒绝覆盖。")
    return {"label": "新产品开发", "counts": counts, "rows": rows, "conflicts": conflicts}


def _validate_local_ids(connection: sqlite3.Connection, aggregate: dict) -> None:
    for key in ("branches", "tasks", "components", "operations"):
        table = f"development_{key}"
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        for row in aggregate[key]:
            if set(row) != columns:
                raise ValueError("开发字段与当前系统不一致，请先升级后再导入。")
            existing = connection.execute(f"SELECT project_id FROM {table} WHERE id = ?", (row["id"],)).fetchone()
            if existing is not None and existing["project_id"] != aggregate["id"]:
                raise ValueError("开发组件或任务 UUID 已属于另一开发单。")
    source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(development_sources)")}
    for row in aggregate["sources"]:
        if set(row) != source_columns:
            raise ValueError("借用来源字段与当前系统不一致。")
        existing = connection.execute("SELECT * FROM development_sources WHERE id=?", (row["id"],)).fetchone()
        if existing is not None and dict(existing) != row:
            raise ValueError("同一借用来源 UUID 内容不同，拒绝修改不可变来源。")
    project_columns = {row["name"] for row in connection.execute("PRAGMA table_info(development_projects)")}
    if set(aggregate["project"]) != project_columns:
        raise ValueError("开发单字段与当前系统不一致，请先升级后再导入。")


def development_state(connection: sqlite3.Connection, incoming: list[dict], root: Path | None) -> dict:
    local = export_aggregates(connection)
    return {"records": local, "suppliers": [dict(row) for row in connection.execute("SELECT * FROM development_suppliers ORDER BY id")], "sources": [dict(row) for row in connection.execute("SELECT * FROM development_sources ORDER BY id")], "files": file_snapshot(root, [*local, *incoming])}


def apply_aggregates(connection: sqlite3.Connection, package_path: Path, incoming: list[dict], root: Path | None, selected: set[str], changes: list) -> tuple[dict, dict]:
    from app.modules.development.repository import sync_import_aggregate
    preview_aggregates(connection, incoming, root)
    local = {row["id"]: row for row in export_aggregates(connection)}
    baseline = file_snapshot(root, [*local.values(), *incoming])
    expected = dict(baseline)
    counts = dict(new=0, updated=0, conflict=0, unchanged=0, ignored_inactive=0)
    accepted = []
    for aggregate in incoming:
        status = aggregate_status(local.get(aggregate["id"]), aggregate)
        if status == "conflict" and aggregate["id"] in selected:
            status = "updated"
        counts[status] += 1
        if status not in {"new", "updated"}:
            continue
        accepted.append(aggregate)
        expected.update({row["storage_path"]: row["sha256"] for row in aggregate["revisions"]})
    allowed_suppliers = accepted_supplier_updates(connection, accepted)
    for aggregate in accepted:
        sync_import_aggregate(connection, aggregate, allowed_supplier_updates=allowed_suppliers)
        import_documents(connection, aggregate)
    accepted_ids = {aggregate["id"] for aggregate in accepted}
    for aggregate in export_aggregates(connection):
        if aggregate["id"] in accepted_ids:
            validate_aggregate(aggregate, connection)
    copy_accepted_files(package_path, root, accepted, changes)
    for table in ("development_projects", "development_branches", "development_tasks", "development_components", "development_operations", "development_documents", "development_document_revisions", "development_document_links"):
        if connection.execute(f"PRAGMA foreign_key_check({table})").fetchone() is not None:
            raise ValueError("开发资料外键依赖或归属校验失败。")
    return counts, {"aggregates": [*local.values(), *incoming], "files": expected}


def verify_applied_files(root: Path | None, verification: dict) -> None:
    if file_snapshot(root, verification["aggregates"]) != verification["files"]:
        raise ValueError("提交前开发文件内容已变化，请重新预览。")


def accepted_supplier_updates(connection: sqlite3.Connection, accepted: list[dict]) -> set[str]:
    project_ids = {aggregate["id"] for aggregate in accepted}
    suppliers = {}
    for aggregate in accepted:
        for row in aggregate["suppliers"]:
            if row["id"] in suppliers and suppliers[row["id"]] != row:
                raise ValueError("数据包不同开发单的同一供应商资料不一致。")
            suppliers[row["id"]] = row
    allowed = set()
    for identifier, row in suppliers.items():
        existing = connection.execute("SELECT * FROM development_suppliers WHERE id=?", (identifier,)).fetchone()
        if existing is None or dict(existing) == row:
            continue
        owners = {record["project_id"] for record in connection.execute("SELECT DISTINCT project_id FROM development_tasks WHERE supplier_id=?", (identifier,))}
        if not owners <= project_ids:
            raise ValueError("供应商变更影响其他开发单；请将所有引用该供应商的开发单一并导出，并逐单确认采用包内资料。")
        allowed.add(identifier)
    return allowed


def aggregate_fields(local: dict, incoming: dict) -> list[dict]:
    from ._schema import FIELD_LABELS
    labels = {"project": "开发单", "branches": "成品", "tasks": "任务", "components": "组件", "operations": "工序", "suppliers": "供应商", "sources": "借用来源", "documents": "文档", "revisions": "文件修订", "links": "采用版本"}
    field_labels = {**FIELD_LABELS, "title": "名称", "owner_username": "负责人", "planned_date": "计划日期", "actual_date": "实际日期", "status": "进度", "task_id": "共用任务", "borrowed_code": "借用编号", "code": "本型号编码", "notes": "备注", "revision_id": "采用修订", "sha256": "文件摘要", "label": "修订版本"}
    result = []
    for key in ("project", *AGGREGATE_KEYS):
        old_rows = [local["project"]] if key == "project" else local[key]
        new_rows = [incoming["project"]] if key == "project" else incoming[key]
        old_index = {row["id"]: row for row in old_rows}
        for row in new_rows:
            before = old_index.get(row["id"], {})
            record_label = row.get("code") or row.get("name") or row.get("title") or row.get("number") or row["id"]
            for field, value in row.items():
                if field in {"id", "version"} or before.get(field) == value:
                    continue
                result.append({"label": f"{labels[key]} {record_label} · {field_labels.get(field, field)}", "before": str(before.get(field, "—")), "after": str(value if value is not None else "—"), "changed": True})
    return result
