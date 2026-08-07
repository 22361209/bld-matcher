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
    CustomerDrawingSlot,
    CustomerDrawingSummary,
    CustomerIdentity,
    CustomerProduct,
    CustomerProductValidationError,
)
from .ports import CustomerDrawingFileAccess, CustomerProductRepository, PreparedCustomerFile


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


def _slot(row: sqlite3.Row, files: Iterable[CustomerDrawingFile] = ()) -> CustomerDrawingSlot:
    return CustomerDrawingSlot(
        id=int(row["id"]),
        customer_product_id=int(row["customer_product_id"]),
        customer_id=int(row["customer_id"]),
        sync_id=str(row["sync_id"]),
        kind=str(row["kind"]),
        current_version=int(row["current_version"]),
        created_by=str(row["created_by"] or ""),
        updated_by=str(row["updated_by"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        files=tuple(files),
    )


def _product(row: sqlite3.Row, drawings: Iterable[CustomerDrawingSlot] = ()) -> CustomerProduct:
    return CustomerProduct(
        id=int(row["id"]),
        customer_id=int(row["customer_id"]),
        sync_id=str(row["sync_id"]),
        bld_no=str(row["bld_no"]),
        customer_product_code=str(row["customer_product_code"] or ""),
        customer_product_name=str(row["customer_product_name"] or ""),
        created_by=str(row["created_by"] or ""),
        updated_by=str(row["updated_by"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        drawings=tuple(drawings),
    )


class SQLiteCustomerProductRepository:
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

    def list_products(self, customer_id: int) -> list[CustomerProduct]:
        rows = self.connection.execute(
            """
            SELECT * FROM customer_products
            WHERE customer_id = ?
            ORDER BY bld_no COLLATE NOCASE, id
            """,
            (customer_id,),
        ).fetchall()
        return self._attach_drawings(rows)

    def get_product(self, customer_id: int, product_id: int) -> CustomerProduct | None:
        row = self.connection.execute(
            "SELECT * FROM customer_products WHERE id = ? AND customer_id = ?",
            (product_id, customer_id),
        ).fetchone()
        if row is None:
            return None
        products = self._attach_drawings([row])
        return products[0]

    def _attach_drawings(self, rows: Sequence[sqlite3.Row]) -> list[CustomerProduct]:
        if not rows:
            return []
        product_ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in product_ids)
        slot_rows = self.connection.execute(
            f"""
            SELECT * FROM customer_drawing_groups
            WHERE customer_product_id IN ({placeholders})
            ORDER BY kind, id
            """,
            product_ids,
        ).fetchall()
        slots = self._attach_files(slot_rows)
        slots_by_product: dict[int, list[CustomerDrawingSlot]] = {product_id: [] for product_id in product_ids}
        for slot in slots:
            slots_by_product[slot.customer_product_id].append(slot)
        return [_product(row, slots_by_product[int(row["id"])]) for row in rows]

    def _attach_files(self, rows: Sequence[sqlite3.Row]) -> list[CustomerDrawingSlot]:
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
        return [_slot(row, files_by_group[int(row["id"])]) for row in rows]

    def insert_product(
        self,
        *,
        customer_id: int,
        sync_id: str,
        bld_no: str,
        customer_product_code: str,
        customer_product_name: str,
        actor: str,
    ) -> int:
        timestamp = now_text()
        try:
            cursor = self.connection.execute(
                """
                INSERT INTO customer_products
                  (customer_id, sync_id, bld_no, customer_product_code, customer_product_name,
                   created_by, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    sync_id,
                    bld_no,
                    customer_product_code,
                    customer_product_name,
                    actor,
                    actor,
                    timestamp,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise CustomerProductValidationError(
                "customer_product.duplicate", "该客户已存在相同 BLD 号的商品。", field="bld_no"
            ) from exc
        if cursor.lastrowid is None:
            raise RuntimeError("Customer product insert did not return an id.")
        return int(cursor.lastrowid)

    def update_product(
        self,
        customer_id: int,
        product_id: int,
        *,
        customer_product_code: str,
        customer_product_name: str,
        actor: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE customer_products
            SET customer_product_code = ?, customer_product_name = ?, updated_by = ?, updated_at = ?
            WHERE id = ? AND customer_id = ?
            """,
            (customer_product_code, customer_product_name, actor, now_text(), product_id, customer_id),
        )
        return cursor.rowcount == 1

    def lock_product_for_delete(self, customer_id: int, product_id: int) -> CustomerProduct | None:
        # SQLite 没有行级锁；删除需要先拿写锁，再读取将被移动/删除的全部文件路径，
        # 避免并发上传在文件快照之后落库而留下孤儿文件。
        self.connection.execute("BEGIN IMMEDIATE")
        return self.get_product(customer_id, product_id)

    def drawing_group_sync_ids(self, customer_id: int) -> set[str]:
        rows = self.connection.execute(
            "SELECT sync_id FROM customer_drawing_groups WHERE customer_id = ?",
            (customer_id,),
        ).fetchall()
        return {str(row["sync_id"]) for row in rows}

    def delete_product(self, customer_id: int, product_id: int) -> bool:
        group_rows = self.connection.execute(
            "SELECT id FROM customer_drawing_groups WHERE customer_product_id = ? AND customer_id = ?",
            (product_id, customer_id),
        ).fetchall()
        group_ids = [int(row["id"]) for row in group_rows]
        if group_ids:
            placeholders = ",".join("?" for _ in group_ids)
            file_rows = self.connection.execute(
                f"SELECT id FROM customer_drawing_files WHERE group_id IN ({placeholders})",
                group_ids,
            ).fetchall()
            file_ids = [int(row["id"]) for row in file_rows]
            if file_ids:
                file_placeholders = ",".join("?" for _ in file_ids)
                self.connection.execute(
                    f"DELETE FROM quote_record_drawings WHERE drawing_file_id IN ({file_placeholders})",
                    file_ids,
                )
            self.connection.execute(
                f"DELETE FROM customer_drawing_files WHERE group_id IN ({placeholders})",
                group_ids,
            )
            self.connection.execute(
                f"DELETE FROM customer_drawing_groups WHERE id IN ({placeholders})",
                group_ids,
            )
        cursor = self.connection.execute(
            "DELETE FROM customer_products WHERE id = ? AND customer_id = ?",
            (product_id, customer_id),
        )
        return cursor.rowcount == 1

    def get_slot(self, customer_id: int, product_id: int, kind: str) -> CustomerDrawingSlot | None:
        row = self.connection.execute(
            """
            SELECT * FROM customer_drawing_groups
            WHERE customer_product_id = ? AND customer_id = ? AND kind = ?
            """,
            (product_id, customer_id, kind),
        ).fetchone()
        if row is None:
            return None
        slots = self._attach_files([row])
        return slots[0]

    def insert_slot(
        self,
        *,
        customer_product_id: int,
        customer_id: int,
        sync_id: str,
        kind: str,
        actor: str,
    ) -> int:
        timestamp = now_text()
        cursor = self.connection.execute(
            """
            INSERT INTO customer_drawing_groups
              (customer_product_id, customer_id, sync_id, kind, current_version,
               created_by, updated_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (customer_product_id, customer_id, sync_id, kind, actor, actor, timestamp, timestamp),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Customer drawing slot insert did not return an id.")
        return int(cursor.lastrowid)

    def claim_next_version(
        self,
        customer_id: int,
        group_id: int,
        *,
        expected_version: int,
        new_version: int,
        actor: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE customer_drawing_groups
            SET current_version = ?, updated_by = ?, updated_at = ?
            WHERE id = ? AND customer_id = ? AND current_version = ?
            """,
            (new_version, actor, now_text(), group_id, customer_id, expected_version),
        )
        return cursor.rowcount == 1

    def set_current_version(self, customer_id: int, group_id: int, version_no: int, *, actor: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE customer_drawing_groups
            SET current_version = ?, updated_by = ?, updated_at = ?
            WHERE id = ? AND customer_id = ?
            """,
            (version_no, actor, now_text(), group_id, customer_id),
        )
        return cursor.rowcount == 1

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

    def get_file_access(self, customer_id: int, file_id: int) -> CustomerDrawingFileAccess | None:
        row = self.connection.execute(
            """
            SELECT f.*, g.customer_id, g.sync_id AS group_sync_id,
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
        )

    def file_references(self, file_ids: Sequence[int]) -> dict[int, CustomerDrawingFileReference]:
        normalized_ids = tuple(dict.fromkeys(int(file_id) for file_id in file_ids))
        if not normalized_ids:
            return {}
        placeholders = ",".join("?" for _ in normalized_ids)
        rows = self.connection.execute(
            f"""
            SELECT f.*, g.customer_id, g.customer_product_id, g.kind, g.current_version,
                   p.bld_no, p.customer_product_name
            FROM customer_drawing_files AS f
            JOIN customer_drawing_groups AS g ON g.id = f.group_id
            JOIN customer_products AS p ON p.id = g.customer_product_id
            WHERE f.id IN ({placeholders})
            """,
            normalized_ids,
        ).fetchall()
        return {
            int(row["id"]): CustomerDrawingFileReference(
                file=_file(row),
                group_id=int(row["group_id"]),
                customer_id=int(row["customer_id"]),
                customer_product_id=int(row["customer_product_id"]),
                kind=str(row["kind"]),
                bld_no=str(row["bld_no"]),
                customer_product_name=str(row["customer_product_name"] or ""),
                current_version=int(row["current_version"]),
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
            WHERE g.customer_id IN ({placeholders})
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
        log_event(self.connection, action, "customer_product", target_key, detail, actor=actor)


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteCustomerProductUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.connection: sqlite3.Connection | None = None
        self.repository: CustomerProductRepository
        self._committed = False

    def __enter__(self) -> SQLiteCustomerProductUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteCustomerProductRepository(self.connection)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Customer product unit of work is not active.")
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
