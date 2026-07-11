from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from app.database import connect, log_event


class SQLiteContractRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def audit(self, action: str, target_type: str, target_key: str, detail: str, *, actor: str) -> None:
        log_event(
            self.connection,
            action,
            target_type,
            target_key,
            detail,
            actor=actor,
        )


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteContractUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.connection: sqlite3.Connection | None = None
        self.repository: SQLiteContractRepository
        self._committed = False

    def __enter__(self) -> SQLiteContractUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteContractRepository(self.connection)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Contract unit of work is not active.")
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
