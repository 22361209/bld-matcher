from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from types import TracebackType

from app.database import connect
from app.platform.audit_store import log_event
from app.platform.clock import now_text

from .domain import (
    CustomerDrawingFile,
    CustomerDrawingFileReference,
    CustomerDrawingGroup,
    CustomerDrawingSummary,
    CustomerIdentity,
)
from .ports import CustomerDrawingFileAccess, CustomerDrawingRepository, PreparedCustomerFile


def _file(row: sqlite3.Row) -> CustomerDrawingFile:
    return CustomerDrawingFile(
        id=int(row["id"]),
        sync_id=str(row["sync_id"]),
        group_id=int(row["group_id"]),
        version_no=int(row["version_no"]),
        revision_label=str(row["revision_label"] or ""),
        original_name=str(row["original_name"]),
        storage_path=str(row["storage_path"]),
        content_type=str(row["content_type"] or "application/octet-stream"),
        size_bytes=int(row["size_bytes"]),
        sha256=str(row["sha256"]),
        uploaded_by=str(row["uploaded_by"] or ""),
        note=str(row["note"] or ""),
        created_at=str(row["created_at"]),
    )


def _group(row: sqlite3.Row, files: Iterable[CustomerDrawingFile] = ()) -> CustomerDrawingGroup:
    return CustomerDrawingGroup(
        id=int(row["id"]),
        customer_id=int(row["customer_id"]),
        sync_id=str(row["sync_id"]),
        direction=str(row["direction"]),
        bld_no=str(row["bld_no"] or ""),
        title=str(row["title"]),
        drawing_no=str(row["drawing_no"] or ""),
        current_version=int(row["current_version"]),
        archived=bool(row["archived"]),
        created_by=str(row["created_by"] or ""),
        updated_by=str(row["updated_by"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        files=tuple(files),
    )


class SQLiteCustomerDrawingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def customer_identity(self, customer_id: int) -> CustomerIdentity | None:
        row = self.connection.execute(
            "SELECT id, name, sync_id FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if row is None:
            return None
        return CustomerIdentity(id=int(row["id"]), name=str(row["name"]), sync_id=str(row["sync_id"] or ""))

    def list_groups(self, customer_id: int, *, include_archived: bool = False) -> list[CustomerDrawingGroup]:
        archived_clause = "" if include_archived else "AND archived = 0"
        rows = self.connection.execute(
            f"""
            SELECT * FROM customer_drawing_groups
            WHERE customer_id = ? {archived_clause}
            ORDER BY direction, archived, updated_at DESC, id DESC
            """,
            (customer_id,),
        ).fetchall()
        return self._attach_files(rows)

    def get_group(self, customer_id: int, group_id: int) -> CustomerDrawingGroup | None:
        row = self.connection.execute(
            "SELECT * FROM customer_drawing_groups WHERE id = ? AND customer_id = ?",
            (group_id, customer_id),
        ).fetchone()
        if row is None:
            return None
        groups = self._attach_files([row])
        return groups[0]

    def _attach_files(self, rows: Sequence[sqlite3.Row]) -> list[CustomerDrawingGroup]:
        if not rows:
            return []
        group_ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in group_ids)
        file_rows = self.connection.execute(
            f"""
            SELECT * FROM customer_drawing_files
            WHERE group_id IN ({placeholders})
            ORDER BY version_no DESC, id ASC
            """,
            group_ids,
        ).fetchall()
        files_by_group: dict[int, list[CustomerDrawingFile]] = {group_id: [] for group_id in group_ids}
        for file_row in file_rows:
            item = _file(file_row)
            files_by_group[item.group_id].append(item)
        return [_group(row, files_by_group[int(row["id"])]) for row in rows]

    def insert_group(
        self,
        *,
        customer_id: int,
        sync_id: str,
        direction: str,
        bld_no: str,
        title: str,
        drawing_no: str,
        current_version: int,
        actor: str,
    ) -> int:
        timestamp = now_text()
        cursor = self.connection.execute(
            """
            INSERT INTO customer_drawing_groups
              (customer_id, sync_id, direction, bld_no, title, drawing_no, current_version,
               archived, created_by, updated_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                customer_id,
                sync_id,
                direction,
                bld_no,
                title,
                drawing_no,
                current_version,
                actor,
                actor,
                timestamp,
                timestamp,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Customer drawing group insert did not return an id.")
        return int(cursor.lastrowid)

    def update_group(
        self,
        customer_id: int,
        group_id: int,
        *,
        direction: str,
        bld_no: str,
        title: str,
        drawing_no: str,
        actor: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE customer_drawing_groups
            SET direction = ?, bld_no = ?, title = ?, drawing_no = ?, updated_by = ?, updated_at = ?
            WHERE id = ? AND customer_id = ? AND archived = 0
            """,
            (direction, bld_no, title, drawing_no, actor, now_text(), group_id, customer_id),
        )
        return cursor.rowcount == 1

    def claim_next_version(
        self,
        customer_id: int,
        group_id: int,
        *,
        expected_version: int,
        actor: str,
    ) -> int | None:
        next_version = expected_version + 1
        cursor = self.connection.execute(
            """
            UPDATE customer_drawing_groups
            SET current_version = ?, updated_by = ?, updated_at = ?
            WHERE id = ? AND customer_id = ? AND current_version = ? AND archived = 0
            """,
            (next_version, actor, now_text(), group_id, customer_id, expected_version),
        )
        return next_version if cursor.rowcount == 1 else None

    def insert_file(
        self,
        group_id: int,
        version_no: int,
        file: PreparedCustomerFile,
        *,
        revision_label: str,
        note: str,
        actor: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO customer_drawing_files
              (group_id, sync_id, version_no, revision_label, original_name, storage_path,
               content_type, size_bytes, sha256, uploaded_by, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_id,
                file.sync_id,
                version_no,
                revision_label,
                file.original_name,
                file.storage_path,
                file.content_type,
                file.size_bytes,
                file.sha256,
                actor,
                note,
                now_text(),
            ),
        )

    def archive_group(self, customer_id: int, group_id: int, *, actor: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE customer_drawing_groups
            SET archived = 1, updated_by = ?, updated_at = ?
            WHERE id = ? AND customer_id = ? AND archived = 0
            """,
            (actor, now_text(), group_id, customer_id),
        )
        return cursor.rowcount == 1

    def unarchive_group(self, customer_id: int, group_id: int, *, actor: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE customer_drawing_groups
            SET archived = 0, updated_by = ?, updated_at = ?
            WHERE id = ? AND customer_id = ? AND archived = 1
            """,
            (actor, now_text(), group_id, customer_id),
        )
        return cursor.rowcount == 1

    def get_file_access(self, customer_id: int, file_id: int) -> CustomerDrawingFileAccess | None:
        row = self.connection.execute(
            """
            SELECT f.*, g.customer_id, g.sync_id AS group_sync_id, g.archived AS group_archived,
                   c.sync_id AS customer_sync_id
            FROM customer_drawing_files AS f
            JOIN customer_drawing_groups AS g ON g.id = f.group_id
            JOIN customers AS c ON c.id = g.customer_id
            WHERE f.id = ? AND g.customer_id = ?
            """,
            (file_id, customer_id),
        ).fetchone()
        if row is None:
            return None
        return CustomerDrawingFileAccess(
            file=_file(row),
            customer_id=int(row["customer_id"]),
            customer_sync_id=str(row["customer_sync_id"] or ""),
            group_sync_id=str(row["group_sync_id"]),
            group_archived=bool(row["group_archived"]),
        )

    def file_references(self, file_ids: Sequence[int]) -> dict[int, CustomerDrawingFileReference]:
        normalized_ids = tuple(dict.fromkeys(int(file_id) for file_id in file_ids))
        if not normalized_ids:
            return {}
        placeholders = ",".join("?" for _ in normalized_ids)
        rows = self.connection.execute(
            f"""
            SELECT f.*, g.customer_id, g.direction, g.title, g.current_version, g.archived AS group_archived
            FROM customer_drawing_files AS f
            JOIN customer_drawing_groups AS g ON g.id = f.group_id
            WHERE f.id IN ({placeholders})
            """,
            normalized_ids,
        ).fetchall()
        return {
            int(row["id"]): CustomerDrawingFileReference(
                file=_file(row),
                group_id=int(row["group_id"]),
                customer_id=int(row["customer_id"]),
                direction=str(row["direction"]),
                title=str(row["title"]),
                current_version=int(row["current_version"]),
                group_archived=bool(row["group_archived"]),
            )
            for row in rows
        }

    def summaries(self, customer_ids: Sequence[int]) -> dict[int, CustomerDrawingSummary]:
        normalized_ids = tuple(dict.fromkeys(int(customer_id) for customer_id in customer_ids))
        if not normalized_ids:
            return {}
        placeholders = ",".join("?" for _ in normalized_ids)
        rows = self.connection.execute(
            f"""
            SELECT g.customer_id,
                   COUNT(DISTINCT g.id) AS group_count,
                   COUNT(f.id) AS file_count
            FROM customer_drawing_groups AS g
            LEFT JOIN customer_drawing_files AS f ON f.group_id = g.id
            WHERE g.archived = 0 AND g.customer_id IN ({placeholders})
            GROUP BY g.customer_id
            """,
            normalized_ids,
        ).fetchall()
        result = {customer_id: CustomerDrawingSummary() for customer_id in normalized_ids}
        for row in rows:
            result[int(row["customer_id"])] = CustomerDrawingSummary(
                group_count=int(row["group_count"]),
                file_count=int(row["file_count"]),
            )
        return result

    def audit(self, action: str, target_key: str, detail: str, *, actor: str) -> None:
        log_event(self.connection, action, "customer_drawing", target_key, detail, actor=actor)


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteCustomerDrawingUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.connection: sqlite3.Connection | None = None
        self.repository: CustomerDrawingRepository
        self._committed = False

    def __enter__(self) -> SQLiteCustomerDrawingUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteCustomerDrawingRepository(self.connection)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Customer drawing unit of work is not active.")
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
