from __future__ import annotations

import sqlite3

from .clock import now_text


def log_event(
    connection: sqlite3.Connection,
    action: str,
    target_type: str,
    target_key: str,
    detail: str = "",
    actor: str = "",
) -> None:
    connection.execute(
        """
        INSERT INTO audit_logs (action, target_type, target_key, actor, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (action, target_type, target_key, actor, detail, now_text()),
    )
