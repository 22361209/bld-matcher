from __future__ import annotations

import sqlite3

from app.matcher import compact_text, normalize_code
from app.platform.audit_store import log_event
from app.platform.clock import now_text


def save_alias(
    connection: sqlite3.Connection,
    source_code: str,
    bld_no: str,
    note: str = "",
    actor: str = "",
) -> None:
    key = normalize_code(source_code)
    timestamp = now_text()
    before = connection.execute("SELECT id FROM aliases WHERE source_code = ?", (key,)).fetchone()
    connection.execute(
        """
        INSERT INTO aliases (source_code, bld_no, note, active, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(source_code) DO UPDATE SET
          bld_no=excluded.bld_no, note=excluded.note, active=1, updated_at=excluded.updated_at
        """,
        (key, compact_text(bld_no), compact_text(note), timestamp, timestamp),
    )
    log_event(
        connection,
        "新增人工映射" if before is None else "编辑人工映射",
        "alias",
        key,
        f"{key} -> {compact_text(bld_no)}",
        actor=actor,
    )
    connection.commit()
