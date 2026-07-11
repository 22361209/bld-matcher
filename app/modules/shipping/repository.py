from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from app.database import connect
from app.platform.audit_store import log_event


class SQLiteShippingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def audit(self, action: str, target_key: str, detail: str, *, actor: str) -> None:
        log_event(
            self.connection,
            action,
            "shipping_notice",
            target_key,
            detail,
            actor=actor,
        )

    def audit_recognition(self, action: str, target_key: str, detail: str, *, actor: str) -> None:
        log_event(
            self.connection,
            action,
            "shipment_recognition",
            target_key,
            detail,
            actor=actor,
        )

    def record_ai_call(self, *, job_id: str, metrics: dict[str, object]) -> None:
        self.connection.execute(
            """
            INSERT INTO ai_provider_calls (
              job_id, provider, model, data_type, caller, status, attempts, latency_ms,
              prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd,
              error_code, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            """,
            (
                job_id,
                str(metrics.get("provider") or ""),
                str(metrics.get("model") or ""),
                str(metrics.get("data_type") or ""),
                str(metrics.get("caller") or ""),
                str(metrics.get("status") or ""),
                int(metrics.get("attempts") or 0),
                int(metrics.get("latency_ms") or 0),
                int(metrics.get("prompt_tokens") or 0),
                int(metrics.get("completion_tokens") or 0),
                int(metrics.get("total_tokens") or 0),
                float(metrics.get("estimated_cost_usd") or 0),
                str(metrics.get("error_code") or ""),
            ),
        )


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteShippingUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.connection: sqlite3.Connection | None = None
        self.repository: SQLiteShippingRepository
        self._committed = False

    def __enter__(self) -> SQLiteShippingUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteShippingRepository(self.connection)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Shipping unit of work is not active.")
        self.connection.commit()
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.connection is None:
            return
        if exc_type is not None or not self._committed:
            self.connection.rollback()
        self.connection.close()
        self.connection = None
