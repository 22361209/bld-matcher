from __future__ import annotations

import sqlite3
from pathlib import Path

from app.platform.audit_store import log_event
from app.platform.clock import now_text

from .domain import fail, validate_aggregate

TABLES = {name: f"development_{name}" for name in
          ("projects", "branches", "tasks", "components", "operations", "suppliers", "sources")}


class SQLiteDevelopmentRepository:
    def __init__(self, connection):
        self.connection = connection

    def get(self, kind, identifier):
        row = self.connection.execute(f"SELECT * FROM {TABLES[kind]} WHERE id=?", (identifier,)).fetchone()
        return dict(row) if row else None

    def rows(self, kind, *, project_id=None):
        where = " WHERE project_id=?" if project_id else ""
        rows = self.connection.execute(f"SELECT * FROM {TABLES[kind]}{where} ORDER BY id",
                                       (project_id,) if project_id else ()).fetchall()
        return [dict(row) for row in rows]

    def _columns(self, kind):
        return {row[1] for row in self.connection.execute(f"PRAGMA table_info({TABLES[kind]})")}

    def insert(self, kind, data):
        if not set(data) <= self._columns(kind):
            fail("开发记录含有未知字段。")
        columns = list(data)
        placeholders = ",".join("?" for _ in columns)
        self.connection.execute(f"INSERT INTO {TABLES[kind]} ({','.join(columns)}) VALUES ({placeholders})",
                                tuple(data[key] for key in columns))

    def update(self, kind, identifier, data):
        if not set(data) <= self._columns(kind) or "id" in data:
            fail("开发记录更新字段无效。")
        if not data:
            return
        clauses = ",".join(f"{key}=?" for key in data)
        self.connection.execute(f"UPDATE {TABLES[kind]} SET {clauses} WHERE id=?", (*data.values(), identifier))

    def claim_project(self, project_id, expected_version):
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        project = self.get("projects", project_id)
        if not project:
            fail("开发单不存在。", code="development.not_found")
        if project["archived"]:
            fail("开发单已归档，资料保留且只读。", code="development.archived")
        try:
            version = int(expected_version)
        except (TypeError, ValueError, OverflowError):
            version = 0
        if isinstance(expected_version, bool) or str(version) != str(expected_version) or version != project["version"]:
            fail("开发资料已由其他人更新，请刷新后重试。", code="development.version_conflict")
        self.connection.execute("UPDATE development_projects SET version=version+1,updated_at=? WHERE id=?",
                                (now_text(), project_id))
        return project

    def export_aggregate(self, project_id):
        project = self.get("projects", project_id)
        if not project:
            fail("开发单不存在。", code="development.not_found")
        result: dict = {"project": project}
        for kind in ("branches", "tasks", "components", "operations"):
            result[kind] = self.rows(kind, project_id=project_id)
        supplier_ids = {row["supplier_id"] for row in result["tasks"] if row["supplier_id"]}
        result["suppliers"] = [self.get("suppliers", identifier) for identifier in sorted(supplier_ids)]
        source_ids = {row["borrowed_source_id"] for row in result["components"] if row["borrowed_source_id"]}
        result["sources"] = [self.get("sources", identifier) for identifier in sorted(source_ids)]
        return result

    def invalidate_confirmations(self, project_id, branch_ids=None):
        sql = """UPDATE development_branches SET confirmed_by='',confirmed_at='',actual_date='',updated_at=?
                 WHERE project_id=?"""
        args = [now_text(), project_id]
        if branch_ids is not None:
            if not branch_ids:
                return
            sql += f" AND id IN ({','.join('?' for _ in branch_ids)})"
            args.extend(branch_ids)
        self.connection.execute(sql, args)

    def has_released_drawing(self, project_id, task_id, *, target_type="task"):
        from .document_repository import SQLiteDevelopmentDocumentRepository
        return SQLiteDevelopmentDocumentRepository(self.connection).has_released_drawing(project_id, target_type, task_id)

    def audit(self, action, project_id, detail, *, actor):
        log_event(self.connection, action, "development_project", project_id, detail, actor=actor)

    def copy_document_links(self, project_id, target_type, source_id, target_id, *, actor):
        from .document_repository import SQLiteDevelopmentDocumentRepository
        SQLiteDevelopmentDocumentRepository(self.connection).copy_target_links(
            project_id, target_type, source_id, target_id, actor=actor)

    def import_aggregate(self, data, *, allowed_supplier_updates=()):
        validate_aggregate(data)
        project_id = data["project"]["id"]
        # A UUID has one owning aggregate forever. Never let an imported row steal it.
        for kind in ("branches", "tasks", "components", "operations"):
            for row in data.get(kind, []):
                existing = self.get(kind, row["id"])
                if existing and existing["project_id"] != project_id:
                    fail("永久标识已属于其他开发单。", code="development.identity_conflict")
        for supplier in data.get("suppliers", []):
            existing = self.get("suppliers", supplier["id"])
            if existing and existing != supplier and supplier["id"] not in allowed_supplier_updates:
                fail("供应商主档已变化，请先处理供应商冲突。", code="development.supplier_conflict")
        for source in data.get("sources", []):
            existing = self.get("sources", source["id"])
            if existing and existing != source:
                fail("同一借用来源永久标识的内容不同，不能替换。", code="development.source_identity_conflict")
        # Deferrable self FK permits parent rows to arrive in any order.
        incoming_project = dict(data["project"])
        current_project = self.get("projects", project_id)
        if current_project:
            incoming_project["version"] = max(current_project["version"], incoming_project["version"]) + 1
        for kind, rows in (("suppliers", data.get("suppliers", [])), ("sources", data.get("sources", [])), ("projects", [incoming_project]),
                           *((kind, data.get(kind, [])) for kind in ("branches", "tasks", "components", "operations"))):
            for row in rows:
                if self.get(kind, row["id"]):
                    self.update(kind, row["id"], {key: value for key, value in row.items() if key != "id"})
                else:
                    self.insert(kind, row)
        validate_aggregate(self.export_aggregate(project_id))


class SQLiteDevelopmentUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory=None):
        self.database_path = Path(database_path)
        self.connection_factory = connection_factory
        self.connection = None

    def __enter__(self):
        # Runtime paths are opened read/write only; this never creates a database or migrates on request.
        if self.connection_factory:
            self.connection = self.connection_factory(self.database_path)
        else:
            self.connection = sqlite3.connect(f"{self.database_path.resolve().as_uri()}?mode=rw", uri=True, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.repository = SQLiteDevelopmentRepository(self.connection)
        from .document_repository import SQLiteDevelopmentDocumentRepository
        self.documents = SQLiteDevelopmentDocumentRepository(self.connection)
        return self

    def commit(self):
        assert self.connection is not None
        self.connection.commit()

    def begin(self):
        assert self.connection is not None
        self.connection.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type, exc, traceback):
        assert self.connection is not None
        self.connection.rollback()
        self.connection.close()


def sync_export_aggregates(connection):
    repository = SQLiteDevelopmentRepository(connection)
    return [repository.export_aggregate(row["id"]) for row in repository.rows("projects")]


def sync_validate_aggregate(data):
    validate_aggregate(data)


def sync_import_aggregate(connection, data, *, allowed_supplier_updates=()):
    SQLiteDevelopmentRepository(connection).import_aggregate(data, allowed_supplier_updates=allowed_supplier_updates)
