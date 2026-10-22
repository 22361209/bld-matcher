"""Development document rows in the portable aggregate contract."""
from __future__ import annotations

import sqlite3
import uuid

from app.modules.development.file_validation import upload_metadata

from ._development_files import revision_path


DOCUMENT_TABLES = {
    "documents": "development_documents",
    "revisions": "development_document_revisions",
    "links": "development_document_links",
}


def export_documents(connection: sqlite3.Connection, project_id: str) -> dict:
    return {
        key: [dict(row) for row in connection.execute(f"SELECT * FROM {table} WHERE project_id = ? ORDER BY id", (project_id,))]
        for key, table in DOCUMENT_TABLES.items()
    }


def validate_documents(aggregate: dict, connection: sqlite3.Connection | None = None) -> None:
    project_id = aggregate["project"]["id"]
    indexes = {}
    for key, table in DOCUMENT_TABLES.items():
        rows = aggregate.get(key)
        if not isinstance(rows, list):
            raise ValueError("开发数据包缺少完整文件修订信息。")
        indexes[key] = {}
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")} if connection else None
        for row in rows:
            try:
                if not isinstance(row, dict) or str(uuid.UUID(row["id"])) != row["id"]:
                    raise ValueError
                if row["project_id"] != project_id or row["id"] in indexes[key]:
                    raise ValueError
                if columns is not None and set(row) != columns:
                    raise ValueError
                indexes[key][row["id"]] = row
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("开发文件编号、归属或字段无效。") from exc
            if connection is not None:
                existing = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (row["id"],)).fetchone()
                if existing is not None and existing["project_id"] != project_id:
                    raise ValueError("开发文件 UUID 已属于另一开发单。")
                if existing is not None and key == "revisions" and dict(existing) != row:
                    raise ValueError("同一开发修订 ID 的内容或元数据不同，拒绝修改不可变历史。")
    documents = indexes["documents"]
    for row in documents.values():
        if row.get("kind") not in {"drawing", "process"} or type(row.get("archived")) is not int or row["archived"] not in {0, 1}:
            raise ValueError("开发文档类型或归档状态无效。")
    revisions = indexes["revisions"]
    for row in revisions.values():
        name, extension, content_type = upload_metadata(str(row.get("original_name", "")), str(row.get("content_type", "")))
        if (name, extension, content_type) != (row.get("original_name"), row.get("extension"), row.get("content_type")):
            raise ValueError("开发修订文件名称或类型未规范化。")
        if type(row.get("revision_no")) is not int or row["revision_no"] <= 0 or row.get("state") not in {"draft", "released", "obsolete"}:
            raise ValueError("开发修订版本或发布状态无效。")
        revision_path(row)
        if row["document_id"] not in documents:
            raise ValueError("开发修订缺少所属文档。")
    targets = {"project": {project_id}}
    for kind, key in (("branch", "branches"), ("task", "tasks"), ("operation", "operations")):
        targets[kind] = {row["id"] for row in aggregate[key]}
    for row in indexes["links"].values():
        revision = revisions.get(row["revision_id"])
        if (
            revision is None or revision["document_id"] != row["document_id"]
            or row["target_id"] not in targets.get(row["target_type"], set())
        ):
            raise ValueError("开发文件采用版本或关联对象归属无效。")
    _validate_completed_drawings(aggregate, documents, revisions, indexes["links"])


def import_documents(connection: sqlite3.Connection, aggregate: dict) -> None:
    validate_documents(aggregate, connection)
    for key, table in DOCUMENT_TABLES.items():
        for row in aggregate[key]:
            names = list(row)
            placeholders = ",".join("?" for _ in names)
            if key == "revisions":
                connection.execute(f"INSERT OR IGNORE INTO {table} ({','.join(names)}) VALUES ({placeholders})", list(row.values()))
            else:
                assignments = ",".join(f"{name}=excluded.{name}" for name in names if name != "id")
                connection.execute(
                    f"INSERT INTO {table} ({','.join(names)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {assignments}",
                    list(row.values()),
                )


def _validate_completed_drawings(aggregate: dict, documents: dict, revisions: dict, links: dict) -> None:
    released_targets = set()
    for link in links.values():
        document = documents[link["document_id"]]
        revision = revisions[link["revision_id"]]
        if document["kind"] == "drawing" and not document["archived"] and revision["state"] == "released":
            released_targets.add((link["target_type"], link["target_id"]))
    tasks = {row["id"]: row for row in aggregate["tasks"]}
    for branch in aggregate["branches"]:
        if not branch.get("confirmed_by"):
            continue
        if branch.get("drawing_required", 1) and ("branch", branch["id"]) not in released_targets:
            raise ValueError("已确认成品缺少固定采用的已发布图纸修订。")
        task_ids = {row["task_id"] for row in aggregate["components"] if row["branch_id"] == branch["id"] and not row.get("archived")}
        if any(tasks[identifier].get("drawing_required", 1) and ("task", identifier) not in released_targets for identifier in task_ids):
            raise ValueError("已确认成品的组件缺少固定采用的已发布图纸修订。")
