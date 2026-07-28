from __future__ import annotations

import sqlite3

from app.platform.clock import now_text

from .domain import Customer


def _customer(row: sqlite3.Row) -> Customer:
    return Customer(
        id=int(row["id"]),
        name=str(row["name"]),
        sync_id=str(row["sync_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def list_customers(connection: sqlite3.Connection) -> list[Customer]:
    rows = connection.execute(
        "SELECT id, name, sync_id, created_at, updated_at FROM customers ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [_customer(row) for row in rows]


def lookup_customers(connection: sqlite3.Connection, query: str, *, limit: int = 20) -> list[Customer]:
    pattern = f"%{query.upper()}%"
    rows = connection.execute(
        """
        SELECT id, name, sync_id, created_at, updated_at FROM customers
        WHERE UPPER(name) LIKE ?
        ORDER BY name COLLATE NOCASE
        LIMIT ?
        """,
        (pattern, limit),
    ).fetchall()
    return [_customer(row) for row in rows]


def get_customer(connection: sqlite3.Connection, customer_id: int) -> Customer | None:
    row = connection.execute(
        "SELECT id, name, sync_id, created_at, updated_at FROM customers WHERE id = ?",
        (customer_id,),
    ).fetchone()
    return _customer(row) if row is not None else None


def find_by_name(connection: sqlite3.Connection, name: str) -> Customer | None:
    row = connection.execute(
        "SELECT id, name, sync_id, created_at, updated_at FROM customers WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    return _customer(row) if row is not None else None


def insert_customer(connection: sqlite3.Connection, *, name: str, sync_id: str) -> Customer:
    cursor = connection.execute(
        "INSERT INTO customers (name, sync_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, sync_id, now_text(), now_text()),
    )
    customer = get_customer(connection, int(cursor.lastrowid))
    assert customer is not None
    return customer


def rename_customer(connection: sqlite3.Connection, customer_id: int, *, name: str) -> None:
    connection.execute(
        "UPDATE customers SET name = ?, updated_at = ? WHERE id = ?",
        (name, now_text(), customer_id),
    )


def delete_customer(connection: sqlite3.Connection, customer_id: int) -> None:
    connection.execute("DELETE FROM customers WHERE id = ?", (customer_id,))


def count_quote_references(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS total FROM quote_records WHERE customer_name = ?",
        (name,),
    ).fetchone()
    return int(row["total"])


def rename_quote_references(connection: sqlite3.Connection, *, old_name: str, new_name: str) -> int:
    cursor = connection.execute(
        "UPDATE quote_records SET customer_name = ?, updated_at = ? WHERE customer_name = ?",
        (new_name, now_text(), old_name),
    )
    return cursor.rowcount
