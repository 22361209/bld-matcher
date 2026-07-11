from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import TracebackType

from app.database import connect
from app.modules.admin.persistence import get_user, get_user_by_username, list_audit_logs, list_log_actors, list_users, save_user
from app.platform.api_keys import (
    create_internal_api_key,
    disable_internal_api_key,
    internal_api_key_status,
    list_internal_api_keys,
)


def _mapping(row: sqlite3.Row | Mapping[str, object] | None) -> dict[str, object] | None:
    return dict(row) if row is not None else None


class SQLiteAdminRepository:
    def __init__(self, connection: sqlite3.Connection, *, api_key_rotation_days: int = 90) -> None:
        self.connection = connection
        self.api_key_rotation_days = api_key_rotation_days

    def users(self) -> list[dict[str, object]]:
        return [dict(row) for row in list_users(self.connection)]

    def user(self, user_id: int) -> dict[str, object] | None:
        return _mapping(get_user(self.connection, user_id))

    def user_by_username(self, username: str) -> dict[str, object] | None:
        return _mapping(get_user_by_username(self.connection, username))

    def save_user(self, data: Mapping[str, object], *, actor: str) -> None:
        save_user(self.connection, dict(data), actor=actor, commit=False)

    def logs(self, *, query: str, actor: str) -> list[dict[str, object]]:
        return [dict(row) for row in list_audit_logs(self.connection, query=query, actor=actor)]

    def log_actors(self) -> list[str]:
        return list_log_actors(self.connection)

    def api_key_page(self) -> tuple[dict[str, object], list[dict[str, object]]]:
        keys = list_internal_api_keys(
            self.connection,
            rotation_days=self.api_key_rotation_days,
        )
        status = internal_api_key_status(self.connection)
        status["rotation_days"] = self.api_key_rotation_days
        status["rotation_due_count"] = sum(bool(key["rotation_due"]) for key in keys)
        return status, keys

    def create_api_key(
        self,
        *,
        actor: str,
        name: str,
        scopes: Iterable[str] | None,
        expires_at: object,
    ) -> str:
        return create_internal_api_key(
            self.connection,
            actor=actor,
            name=name,
            scopes=scopes,
            expires_at=expires_at,
            commit=False,
        )

    def disable_api_key(self, *, actor: str, key_id: int | None) -> bool:
        return disable_internal_api_key(
            self.connection,
            actor=actor,
            key_id=key_id,
            commit=False,
        )


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteAdminUnitOfWork:
    def __init__(
        self,
        database_path: Path,
        *,
        connection_factory: ConnectionFactory = connect,
        api_key_rotation_days: int = 90,
    ) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.api_key_rotation_days = api_key_rotation_days
        self.connection: sqlite3.Connection | None = None
        self.repository: SQLiteAdminRepository
        self._committed = False

    def __enter__(self) -> SQLiteAdminUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteAdminRepository(
            self.connection,
            api_key_rotation_days=self.api_key_rotation_days,
        )
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Admin unit of work is not active.")
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
