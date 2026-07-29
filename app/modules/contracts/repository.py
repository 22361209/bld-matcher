from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from app.database import connect
from app.platform.audit_store import log_event
from app.platform.clock import now_text


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

    @staticmethod
    def _document(row: sqlite3.Row | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "contract_type": str(row["contract_type"]),
            "contract_no": str(row["contract_no"]),
            "customer_id": int(row["customer_id"]) if row["customer_id"] is not None else None,
            "customer_name": str(row["customer_name"]),
            "source_quote_no": str(row["source_quote_no"]),
            "language": str(row["language"]),
            "currency": str(row["currency"]),
            "source_snapshot_json": str(row["source_snapshot_json"]),
            "source_snapshot_sha256": str(row["source_snapshot_sha256"]),
            "file_path": str(row["file_path"]),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"]),
        }

    def add_document(
        self,
        *,
        contract_type: str,
        contract_no: str,
        customer_id: int | None,
        customer_name: str,
        source_quote_no: str,
        language: str,
        currency: str,
        file_path: str,
        source_snapshot_json: str = "",
        source_snapshot_sha256: str = "",
        actor: str,
    ) -> dict[str, object]:
        cursor = self.connection.execute(
            """
            INSERT INTO contract_documents
              (contract_type, contract_no, customer_id, customer_name, source_quote_no,
               language, currency, source_snapshot_json, source_snapshot_sha256,
               file_path, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract_type,
                contract_no,
                customer_id,
                customer_name,
                source_quote_no,
                language,
                currency,
                source_snapshot_json,
                source_snapshot_sha256,
                file_path,
                actor,
                now_text(),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Created contract document did not return an ID.")
        document = self.get_document(int(cursor.lastrowid))
        if document is None:
            raise RuntimeError("Created contract document could not be reloaded.")
        return document

    def get_document(self, document_id: int) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM contract_documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        return self._document(row)

    def list_documents_by_quote_no(self, quote_no: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT * FROM contract_documents
            WHERE source_quote_no = ? AND contract_type = 'sales'
            ORDER BY created_at DESC, id DESC
            """,
            (quote_no,),
        ).fetchall()
        return [document for row in rows if (document := self._document(row)) is not None]

    def list_documents_by_customer(
        self,
        customer_id: int,
        *,
        customer_name: str = "",
        limit: int = 50,
    ) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT * FROM contract_documents
            WHERE contract_type = 'sales'
              AND (customer_id = ? OR (customer_id IS NULL AND customer_name = ? COLLATE NOCASE))
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (customer_id, customer_name, max(1, min(200, int(limit)))),
        ).fetchall()
        return [document for row in rows if (document := self._document(row)) is not None]


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
