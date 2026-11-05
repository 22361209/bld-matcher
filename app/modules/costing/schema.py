"""Additive cost schema shared by explicit trial and main-database migrations."""
from __future__ import annotations

import sqlite3


COSTING_TABLES = ("costing_records", "costing_revisions", "costing_reference_data")
SCHEMA = """
CREATE TABLE IF NOT EXISTS costing_records (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind TEXT NOT NULL CHECK(kind IN ('arm', 'ball')),
    name TEXT NOT NULL,
    brand TEXT NOT NULL,
    models TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ready', 'draft')),
    version INTEGER NOT NULL CHECK(version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS costing_records_status ON costing_records(status, updated_at);
CREATE TABLE IF NOT EXISTS costing_revisions (
    id INTEGER PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES costing_records(id),
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL,
    UNIQUE(record_id, version)
);
CREATE TABLE IF NOT EXISTS costing_reference_data (
    name TEXT PRIMARY KEY CHECK(name = 'workbook'),
    payload_json TEXT NOT NULL
);
"""


def install_schema(connection: sqlite3.Connection) -> None:
    """Install only cost tables; the caller owns transaction and version tracking."""
    # executescript would implicitly commit the migration transaction.
    for statement in SCHEMA.split(";"):
        if statement.strip():
            connection.execute(statement)
