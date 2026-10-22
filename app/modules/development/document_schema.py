from __future__ import annotations

import sqlite3


def create_schema(connection: sqlite3.Connection) -> None:
    # execute one statement at a time: executescript would commit the migration transaction.
    statements = (
        """CREATE TABLE IF NOT EXISTS development_documents (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES development_projects(id),
            kind TEXT NOT NULL CHECK(kind IN ('drawing','process')),
            number TEXT NOT NULL, title TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
            created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(id, project_id)
        )""",
        """CREATE TABLE IF NOT EXISTS development_document_revisions (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL, project_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL CHECK(revision_no > 0),
            label TEXT NOT NULL, change_note TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('draft','released','obsolete')),
            original_name TEXT NOT NULL, extension TEXT NOT NULL,
            content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
            sha256 TEXT NOT NULL CHECK(length(sha256) = 64), storage_path TEXT NOT NULL UNIQUE,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(document_id,project_id) REFERENCES development_documents(id,project_id),
            UNIQUE(document_id,revision_no), UNIQUE(id,document_id,project_id)
        )""",
        """CREATE TABLE IF NOT EXISTS development_document_links (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            target_type TEXT NOT NULL CHECK(target_type IN ('project','branch','task','operation')),
            target_id TEXT NOT NULL, document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
            updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(document_id,project_id) REFERENCES development_documents(id,project_id),
            FOREIGN KEY(revision_id,document_id,project_id)
                REFERENCES development_document_revisions(id,document_id,project_id),
            UNIQUE(project_id,target_type,target_id,document_id)
        )""",
        "CREATE INDEX IF NOT EXISTS development_document_project ON development_documents(project_id)",
        "CREATE INDEX IF NOT EXISTS development_link_target ON development_document_links(project_id,target_type,target_id)",
    )
    for statement in statements:
        connection.execute(statement)
    immutable = (
        "id", "document_id", "project_id", "revision_no", "label", "change_note", "state",
        "original_name", "extension", "content_type", "size_bytes", "sha256", "storage_path",
        "created_by", "created_at",
    )
    conditions = " OR ".join(f"OLD.{key} IS NOT NEW.{key}" for key in immutable)
    connection.execute(f"""CREATE TRIGGER IF NOT EXISTS development_revision_immutable
        BEFORE UPDATE ON development_document_revisions WHEN {conditions}
        BEGIN SELECT RAISE(ABORT, 'development revision is immutable'); END""")
