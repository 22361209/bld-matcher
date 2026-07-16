from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from app.database import connect
from app.platform.audit_store import log_event
from app.platform.clock import now_text

from .domain import QuoteDraft, QuoteFilters, QuoteRecord, QuoteStats


def _record(row: sqlite3.Row | None) -> QuoteRecord | None:
    if row is None:
        return None
    return QuoteRecord(
        id=int(row["id"]),
        customer_name=str(row["customer_name"]),
        bld_no=str(row["bld_no"] or row["product_model"]),
        customer_product_code=str(row["customer_product_code"] or ""),
        product_model=str(row["product_model"]),
        price=float(row["price"]),
        tax_price=float(row["tax_price"]) if row["tax_price"] is not None else None,
        net_price=float(row["net_price"]) if row["net_price"] is not None else None,
        currency=str(row["currency"]),
        moq=int(row["moq"]) if row["moq"] is not None else None,
        quote_date=str(row["quote_date"]),
        quoted_by=str(row["quoted_by"] or ""),
        source_type=str(row["source_type"]),
        source_text=str(row["source_text"] or ""),
        attachment_path=str(row["attachment_path"] or ""),
        remark=str(row["remark"] or ""),
        version=int(row["version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _filter_clauses(filters: QuoteFilters) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if filters.customer_name:
        clauses.append("UPPER(customer_name) LIKE ?")
        params.append(f"%{filters.customer_name.upper()}%")
    if filters.bld_no:
        clauses.append("UPPER(COALESCE(NULLIF(bld_no, ''), product_model)) LIKE ?")
        params.append(f"%{filters.bld_no.upper()}%")
    if filters.date_from:
        clauses.append("quote_date >= ?")
        params.append(filters.date_from)
    if filters.date_to:
        clauses.append("quote_date <= ?")
        params.append(filters.date_to)
    if filters.currency:
        clauses.append("currency = ?")
        params.append(filters.currency)
    if filters.quoted_by:
        clauses.append("UPPER(quoted_by) LIKE ?")
        params.append(f"%{filters.quoted_by.upper()}%")
    return clauses, params


class SQLiteQuoteRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, draft: QuoteDraft) -> QuoteRecord:
        values = draft.storage_values()
        cursor = self.connection.execute(
            """
            INSERT INTO quote_records
              (sync_id, customer_name, bld_no, customer_product_code, product_model, price, tax_price, net_price,
               currency, moq, quote_date, quoted_by, source_type, source_text, attachment_path, remark,
               version, created_at, updated_at)
            VALUES
              (:sync_id, :customer_name, :bld_no, :customer_product_code, :product_model, :price, :tax_price, :net_price,
               :currency, :moq, :quote_date, :quoted_by, :source_type, :source_text, :attachment_path, :remark,
               1, :created_at, :updated_at)
            """,
            {**values, "sync_id": uuid.uuid4().hex},
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Created quote did not return an ID.")
        record = self.get(int(cursor.lastrowid))
        if record is None:
            raise RuntimeError("Created quote could not be reloaded.")
        return record

    def get(self, quote_id: int) -> QuoteRecord | None:
        return _record(self.connection.execute("SELECT * FROM quote_records WHERE id = ?", (quote_id,)).fetchone())

    def list(self, filters: QuoteFilters, *, limit: int, offset: int) -> list[QuoteRecord]:
        sql = "SELECT * FROM quote_records"
        clauses, params = _filter_clauses(filters)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY quote_date DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([max(1, min(500, limit)), max(0, offset)])
        records = [_record(row) for row in self.connection.execute(sql, params).fetchall()]
        return [record for record in records if record is not None]

    def count(self, filters: QuoteFilters) -> int:
        sql = "SELECT COUNT(*) FROM quote_records"
        clauses, params = _filter_clauses(filters)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return int(self.connection.execute(sql, params).fetchone()[0] or 0)

    def latest(self, *, customer_name: str, bld_no: str) -> QuoteRecord | None:
        return _record(
            self.connection.execute(
                """
                SELECT * FROM quote_records
                WHERE customer_name = ? AND COALESCE(NULLIF(bld_no, ''), product_model) = ?
                ORDER BY quote_date DESC, id DESC
                LIMIT 1
                """,
                (customer_name, bld_no),
            ).fetchone()
        )

    def update(self, quote_id: int, draft: QuoteDraft, *, expected_version: int) -> QuoteRecord | None:
        values = draft.storage_values()
        values.update({"id": quote_id, "expected_version": expected_version})
        cursor = self.connection.execute(
            """
            UPDATE quote_records
            SET customer_name=:customer_name, bld_no=:bld_no, customer_product_code=:customer_product_code,
                product_model=:product_model, price=:price, tax_price=:tax_price, net_price=:net_price,
                currency=:currency, moq=:moq, quote_date=:quote_date, quoted_by=:quoted_by,
                source_type=:source_type, source_text=:source_text, attachment_path=:attachment_path,
                remark=:remark, version=version + 1, updated_at=:updated_at
            WHERE id=:id AND version=:expected_version
            """,
            values,
        )
        return self.get(quote_id) if cursor.rowcount == 1 else None

    def add_revision(self, before: QuoteRecord, after: QuoteRecord, *, actor: str) -> None:
        self.connection.execute(
            """
            INSERT INTO quote_record_revisions (quote_id, changed_by, before_json, after_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                before.id,
                actor,
                json.dumps(before.legacy_payload(), ensure_ascii=False, sort_keys=True),
                json.dumps(after.legacy_payload(), ensure_ascii=False, sort_keys=True),
                now_text(),
            ),
        )

    def stats(self) -> QuoteStats:
        row = self.connection.execute(
            """
            SELECT
              COUNT(*) AS total,
              COUNT(DISTINCT customer_name) AS customers,
              COUNT(DISTINCT COALESCE(NULLIF(bld_no, ''), product_model)) AS models
            FROM quote_records
            """
        ).fetchone()
        return QuoteStats(
            total=int(row["total"] or 0),
            customers=int(row["customers"] or 0),
            models=int(row["models"] or 0),
        )

    def audit(self, action: str, quote: QuoteRecord, *, actor: str) -> None:
        log_event(
            self.connection,
            action,
            "quote_record",
            str(quote.id),
            f"{quote.customer_name} {quote.bld_no}",
            actor=actor,
        )


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteQuoteUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.connection: sqlite3.Connection | None = None
        self.repository: SQLiteQuoteRepository
        self._committed = False

    def __enter__(self) -> SQLiteQuoteUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteQuoteRepository(self.connection)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Quote unit of work is not active.")
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
