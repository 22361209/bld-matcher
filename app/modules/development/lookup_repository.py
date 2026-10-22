from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .lookup_ports import DevelopmentLookupRepository


class SQLiteDevelopmentLookupRepository:
    """Read only development snapshots; never loads legacy product/material bootstraps."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def project(self, project_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT id,version,archived FROM development_projects WHERE id=?", (project_id,),
        ).fetchone()
        return dict(row) if row else None

    def component(self, project_id: str, component_id: str) -> dict | None:
        row = self.connection.execute(
            """SELECT component.id,component.branch_id,component.task_id,component.code,component.name,
                      component.borrowed_code,component.borrowed_source_id,branch.code AS branch_code
               FROM development_components component
               JOIN development_branches branch ON branch.id=component.branch_id
                    AND branch.project_id=component.project_id
               JOIN development_tasks task ON task.id=component.task_id AND task.project_id=component.project_id
               WHERE component.project_id=? AND component.id=? AND component.archived=0
                 AND branch.archived=0 AND task.archived=0""", (project_id, component_id),
        ).fetchone()
        return dict(row) if row else None

    def sources(self, query: str, *, limit: int) -> list[dict]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.connection.execute(
            """SELECT id,code,name FROM development_sources
               WHERE code LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\'
               ORDER BY CASE WHEN lower(code)=lower(?) THEN 0 ELSE 1 END,code COLLATE NOCASE,name,id LIMIT ?""",
            (f"%{escaped}%", f"%{escaped}%", query, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def task_suggestions(self, source_id: str, *, limit: int) -> list[dict]:
        rows = self.connection.execute(
            """WITH matches AS (
                   SELECT task.id,MIN(component.id) AS component_id FROM development_components component
                   JOIN development_tasks task ON task.id=component.task_id AND task.project_id=component.project_id
                   JOIN development_branches branch ON branch.id=component.branch_id AND branch.project_id=component.project_id
                   JOIN development_projects project ON project.id=component.project_id
                   WHERE component.borrowed_source_id=? AND component.archived=0 AND task.archived=0
                     AND branch.archived=0 AND project.archived=0
                   GROUP BY task.id ORDER BY task.id LIMIT ?)
               SELECT task.id AS task_id,task.project_id,project.title AS project_title,
                      component.id AS component_id,component.code AS component_code,
                      task.name,task.procurement_mode,task.material_category,task.material_grade,
                      task.forming_process,task.notes,supplier.id AS supplier_id,COALESCE(supplier.name,'') AS supplier_name
               FROM matches JOIN development_tasks task ON task.id=matches.id
               JOIN development_components component ON component.id=matches.component_id
               JOIN development_projects project ON project.id=task.project_id
               LEFT JOIN development_suppliers supplier ON supplier.id=task.supplier_id AND supplier.archived=0
               ORDER BY task.id""", (source_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def adopted_documents(self, project_id: str, task_id: str) -> list[dict]:
        rows = self.connection.execute(
            """SELECT document.id AS document_id,document.kind,document.number,document.title,document.archived,
                      revision.id AS revision_id,revision.label,revision.state,revision.original_name,
                      revision.content_type,revision.revision_no,
                      EXISTS(SELECT 1 FROM development_document_revisions newer
                             WHERE newer.document_id=document.id AND newer.revision_no>revision.revision_no) AS has_newer_revision
               FROM development_document_links link
               JOIN development_documents document ON document.id=link.document_id AND document.project_id=link.project_id
               JOIN development_document_revisions revision ON revision.id=link.revision_id
                    AND revision.document_id=document.id AND revision.project_id=link.project_id
               WHERE link.project_id=? AND link.target_type='task' AND link.target_id=?
               ORDER BY document.kind,document.number,document.id""", (project_id, task_id),
        ).fetchall()
        return [dict(row) for row in rows]


@contextmanager
def open_lookup_repository(database_path: Path) -> Iterator[DevelopmentLookupRepository]:
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield SQLiteDevelopmentLookupRepository(connection)
    finally:
        connection.rollback()
        connection.close()
