from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from uuid import uuid4

from app.platform.audit_store import log_event
from app.platform.clock import now_text

from .document_domain import DevelopmentDocumentError, identity


def validate_target(connection: sqlite3.Connection, project_id: str, target_type: str, target_id: str) -> None:
    identity(project_id)
    identity(target_id)
    tables = {"project": "development_projects", "branch": "development_branches",
              "task": "development_tasks", "operation": "development_operations"}
    table = tables.get(target_type)
    if table is None:
        raise DevelopmentDocumentError("development.document_target", "资料关联对象无效。")
    clause, values = ("id = ?", (project_id,)) if target_type == "project" else (
        "id = ? AND project_id = ?", (target_id, project_id),
    )
    if (target_type == "project" and target_id != project_id) or connection.execute(
        f"SELECT id FROM {table} WHERE {clause}", values,
    ).fetchone() is None:
        raise DevelopmentDocumentError("development.document_target", "资料关联对象不属于当前开发单。")


class SQLiteDevelopmentDocumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def project_exists(self, project_id: str) -> None:
        validate_target(self.connection, project_id, "project", project_id)

    def document(self, project_id: str, document_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM development_documents WHERE id = ? AND project_id = ?", (document_id, project_id),
        ).fetchone()
        if row is None:
            raise DevelopmentDocumentError("development.document_not_found", "开发资料不存在。")
        return dict(row)

    def revision(self, project_id: str, revision_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM development_document_revisions WHERE id = ? AND project_id = ?", (revision_id, project_id),
        ).fetchone()
        if row is None:
            raise DevelopmentDocumentError("development.revision_not_found", "资料修订不存在。")
        return dict(row)

    def list_documents(self, project_id: str, *, target_type: str | None, target_id: str | None) -> list[dict]:
        self.project_exists(project_id)
        if target_type is not None or target_id is not None:
            validate_target(self.connection, project_id, str(target_type or ""), str(target_id or ""))
        documents = self.connection.execute(
            "SELECT * FROM development_documents WHERE project_id = ? ORDER BY archived,kind,number,id", (project_id,),
        ).fetchall()
        result = []
        for row in documents:
            item = dict(row)
            revisions = [dict(value) for value in self.connection.execute(
                "SELECT * FROM development_document_revisions WHERE document_id = ? ORDER BY revision_no DESC", (item["id"],),
            ).fetchall()]
            links = [dict(value) for value in self.connection.execute(
                "SELECT * FROM development_document_links WHERE document_id = ? ORDER BY target_type,target_id", (item["id"],),
            ).fetchall()]
            if target_type is not None:
                links = [link for link in links if link["target_type"] == target_type and link["target_id"] == target_id]
                if not links:
                    continue
            latest = revisions[0]["id"] if revisions else None
            for revision in revisions:
                revision.pop("storage_path", None)
                revision["previewable"] = revision["content_type"] == "application/pdf"
            for link in links:
                link["has_newer_revision"] = link["revision_id"] != latest
            item.update(revisions=revisions, links=links)
            result.append(item)
        return result

    def create_document(self, project_id: str, data: Mapping[str, object], *, actor: str) -> dict:
        value = {"id": str(uuid4()), "project_id": project_id, "kind": data["kind"], "number": data["number"],
                 "title": data["title"], "archived": 0, "created_by": actor, "created_at": now_text()}
        self.connection.execute(
            """INSERT INTO development_documents(id,project_id,kind,number,title,archived,created_by,created_at)
               VALUES(:id,:project_id,:kind,:number,:title,:archived,:created_by,:created_at)""", value,
        )
        return value

    def insert_revision(self, project_id: str, document_id: str, data: Mapping[str, object], *, actor: str) -> dict:
        number = self.connection.execute(
            "SELECT COALESCE(MAX(revision_no),0)+1 FROM development_document_revisions WHERE document_id = ?", (document_id,),
        ).fetchone()[0]
        value = dict(data, project_id=project_id, document_id=document_id, revision_no=number,
                     created_by=actor, created_at=now_text())
        columns = ("id", "document_id", "project_id", "revision_no", "label", "change_note", "state", "original_name",
                   "extension", "content_type", "size_bytes", "sha256", "storage_path", "created_by", "created_at")
        self.connection.execute(
            f"INSERT INTO development_document_revisions({','.join(columns)}) VALUES({','.join(':'+key for key in columns)})", value,
        )
        return self.revision(project_id, str(value["id"]))

    def adopt(self, project_id: str, revision_id: str, *, target_type: str, target_id: str, actor: str) -> dict:
        validate_target(self.connection, project_id, target_type, target_id)
        revision = self.revision(project_id, revision_id)
        document = self.document(project_id, revision["document_id"])
        if document["archived"]:
            raise DevelopmentDocumentError("development.document_archived", "已归档资料不能重新采用。")
        if revision["state"] == "obsolete":
            raise DevelopmentDocumentError("development.revision_obsolete", "作废修订不能采用。")
        value = {"id": str(uuid4()), "project_id": project_id, "target_type": target_type,
                 "target_id": target_id, "document_id": revision["document_id"], "revision_id": revision_id,
                 "updated_by": actor, "updated_at": now_text()}
        self.connection.execute(
            """INSERT INTO development_document_links
               (id,project_id,target_type,target_id,document_id,revision_id,updated_by,updated_at)
               VALUES(:id,:project_id,:target_type,:target_id,:document_id,:revision_id,:updated_by,:updated_at)
               ON CONFLICT(project_id,target_type,target_id,document_id) DO UPDATE SET
               revision_id=excluded.revision_id,updated_by=excluded.updated_by,updated_at=excluded.updated_at""", value,
        )
        return dict(self.connection.execute(
            """SELECT * FROM development_document_links WHERE project_id=? AND target_type=?
               AND target_id=? AND document_id=?""", (project_id, target_type, target_id, revision["document_id"]),
        ).fetchone())

    def archive(self, project_id: str, document_id: str) -> None:
        self.document(project_id, document_id)
        self.connection.execute("UPDATE development_documents SET archived=1 WHERE id=? AND project_id=?", (document_id, project_id))

    def audit(self, action: str, project_id: str, detail: str, *, actor: str) -> None:
        log_event(self.connection, action, "development_document", project_id, detail, actor=actor)

    def has_released_drawing(self, project_id: str, target_type: str, target_id: str) -> bool:
        return self.connection.execute(
            """SELECT 1 FROM development_document_links link
               JOIN development_documents doc ON doc.id=link.document_id
               JOIN development_document_revisions revision ON revision.id=link.revision_id
               WHERE link.project_id=? AND link.target_type=? AND link.target_id=?
                 AND doc.kind='drawing' AND doc.archived=0 AND revision.state='released' LIMIT 1""",
            (project_id, target_type, target_id),
        ).fetchone() is not None

    def released_drawings(self, project_id: str, target_type: str, target_id: str) -> list[dict]:
        rows = self.connection.execute(
            """SELECT revision.* FROM development_document_links link
               JOIN development_documents doc ON doc.id=link.document_id
               JOIN development_document_revisions revision ON revision.id=link.revision_id
               WHERE link.project_id=? AND link.target_type=? AND link.target_id=?
                 AND doc.kind='drawing' AND doc.archived=0 AND revision.state='released'""",
            (project_id, target_type, target_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def copy_task_links(self, project_id: str, task_map: Mapping[str, Sequence[str]], *, actor: str) -> None:
        for source_task_id, target_ids in task_map.items():
            for target_task_id in target_ids:
                copy_task_links(self.connection, project_id, source_task_id, target_task_id, actor=actor)

    def copy_target_links(self, project_id: str, target_type: str, source_id: str, target_id: str, *, actor: str) -> None:
        validate_target(self.connection, project_id, target_type, source_id)
        validate_target(self.connection, project_id, target_type, target_id)
        rows = self.connection.execute(
            "SELECT revision_id FROM development_document_links WHERE project_id=? AND target_type=? AND target_id=?",
            (project_id, target_type, source_id),
        ).fetchall()
        for row in rows:
            self.adopt(project_id, row["revision_id"], target_type=target_type, target_id=target_id, actor=actor)


def missing_required_drawings(
    connection: sqlite3.Connection, project_id: str, task_ids: Sequence[str], branch_id: str | None = None,
) -> list[str]:
    repository = SQLiteDevelopmentDocumentRepository(connection)
    return [task_id for task_id in dict.fromkeys(task_ids) if not repository.has_released_drawing(project_id, "task", task_id)]


def copy_task_links(connection: sqlite3.Connection, project_id: str, source_task_id: str, target_task_id: str, *, actor: str) -> None:
    repository = SQLiteDevelopmentDocumentRepository(connection)
    validate_target(connection, project_id, "task", source_task_id)
    validate_target(connection, project_id, "task", target_task_id)
    rows = connection.execute(
        "SELECT revision_id FROM development_document_links WHERE project_id=? AND target_type='task' AND target_id=?",
        (project_id, source_task_id),
    ).fetchall()
    for row in rows:
        repository.adopt(project_id, row["revision_id"], target_type="task", target_id=target_task_id, actor=actor)
