from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from app.platform.clock import now_text

from .domain import Customer, CustomerContact


CUSTOMER_COLUMNS = "id, name, sync_id, code, status, owner_username, created_at, updated_at"


def _customer(row: sqlite3.Row) -> Customer:
    return Customer(
        id=int(row["id"]),
        name=str(row["name"]),
        sync_id=str(row["sync_id"]),
        code=str(row["code"] or ""),
        status=str(row["status"] or "active"),
        owner_username=str(row["owner_username"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _contact(row: sqlite3.Row) -> CustomerContact:
    return CustomerContact(
        id=int(row["id"]),
        customer_id=int(row["customer_id"]),
        name=str(row["name"]),
        title=str(row["title"] or ""),
        role=str(row["role"] or ""),
        phone=str(row["phone"] or ""),
        email=str(row["email"] or ""),
        wechat=str(row["wechat"] or ""),
        is_primary=bool(row["is_primary"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def list_customers(
    connection: sqlite3.Connection,
    *,
    query: str = "",
    status: str = "",
    owner_usernames: Sequence[str] = (),
) -> list[Customer]:
    clauses: list[str] = []
    values: list[object] = []
    if query:
        query_clauses = [
            "UPPER(name) LIKE ?",
            "UPPER(code) LIKE ?",
            "UPPER(owner_username) LIKE ?",
        ]
        pattern = f"%{query.upper()}%"
        values.extend([pattern, pattern, pattern])
        if owner_usernames:
            placeholders = ",".join("?" for _username in owner_usernames)
            query_clauses.append(f"owner_username IN ({placeholders})")
            values.extend(owner_usernames)
        clauses.append(f"({' OR '.join(query_clauses)})")
    if status:
        clauses.append("status = ?")
        values.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT {CUSTOMER_COLUMNS} FROM customers{where} ORDER BY name COLLATE NOCASE",
        values,
    ).fetchall()
    return [_customer(row) for row in rows]


def lookup_customers(connection: sqlite3.Connection, query: str, *, limit: int = 20) -> list[Customer]:
    pattern = f"%{query.upper()}%"
    rows = connection.execute(
        f"""
        SELECT {CUSTOMER_COLUMNS} FROM customers
        WHERE status = 'active' AND UPPER(name) LIKE ?
        ORDER BY name COLLATE NOCASE
        LIMIT ?
        """,
        (pattern, limit),
    ).fetchall()
    return [_customer(row) for row in rows]


def get_customer(connection: sqlite3.Connection, customer_id: int) -> Customer | None:
    row = connection.execute(
        f"SELECT {CUSTOMER_COLUMNS} FROM customers WHERE id = ?",
        (customer_id,),
    ).fetchone()
    return _customer(row) if row is not None else None


def find_by_name(
    connection: sqlite3.Connection,
    name: str,
    *,
    active_only: bool = False,
) -> Customer | None:
    active_clause = " AND status = 'active'" if active_only else ""
    row = connection.execute(
        f"SELECT {CUSTOMER_COLUMNS} FROM customers WHERE name = ? COLLATE NOCASE{active_clause}",
        (name,),
    ).fetchone()
    return _customer(row) if row is not None else None


def find_by_code(connection: sqlite3.Connection, code: str) -> Customer | None:
    row = connection.execute(
        f"SELECT {CUSTOMER_COLUMNS} FROM customers WHERE code = ? COLLATE NOCASE",
        (code,),
    ).fetchone()
    return _customer(row) if row is not None else None


def insert_customer(
    connection: sqlite3.Connection,
    *,
    name: str,
    sync_id: str,
    code: str = "",
    owner_username: str = "",
) -> Customer:
    cursor = connection.execute(
        """
        INSERT INTO customers (name, sync_id, code, status, owner_username, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?, ?)
        """,
        (name, sync_id, code, owner_username, now_text(), now_text()),
    )
    customer = get_customer(connection, int(cursor.lastrowid))
    assert customer is not None
    return customer


def update_customer_profile(
    connection: sqlite3.Connection,
    customer_id: int,
    *,
    name: str,
    code: str,
    owner_username: str,
) -> None:
    connection.execute(
        """
        UPDATE customers
        SET name = ?, code = ?, owner_username = ?, updated_at = ?
        WHERE id = ?
        """,
        (name, code, owner_username, now_text(), customer_id),
    )


def set_customer_status(connection: sqlite3.Connection, customer_id: int, *, status: str) -> None:
    connection.execute(
        "UPDATE customers SET status = ?, updated_at = ? WHERE id = ?",
        (status, now_text(), customer_id),
    )


def list_contacts(connection: sqlite3.Connection, customer_id: int) -> list[CustomerContact]:
    rows = connection.execute(
        """
        SELECT id, customer_id, name, title, role, phone, email, wechat,
               is_primary, created_at, updated_at
        FROM customer_contacts
        WHERE customer_id = ?
        ORDER BY is_primary DESC, name COLLATE NOCASE, id
        """,
        (customer_id,),
    ).fetchall()
    return [_contact(row) for row in rows]


def primary_contacts(connection: sqlite3.Connection, customer_ids: list[int]) -> dict[int, CustomerContact]:
    if not customer_ids:
        return {}
    placeholders = ",".join("?" for _ in customer_ids)
    rows = connection.execute(
        f"""
        SELECT id, customer_id, name, title, role, phone, email, wechat,
               is_primary, created_at, updated_at
        FROM customer_contacts
        WHERE customer_id IN ({placeholders}) AND is_primary = 1
        ORDER BY customer_id, is_primary DESC, id
        """,
        customer_ids,
    ).fetchall()
    contacts: dict[int, CustomerContact] = {}
    for row in rows:
        contact = _contact(row)
        contacts.setdefault(contact.customer_id, contact)
    return contacts


def get_contact(
    connection: sqlite3.Connection,
    customer_id: int,
    contact_id: int,
) -> CustomerContact | None:
    row = connection.execute(
        """
        SELECT id, customer_id, name, title, role, phone, email, wechat,
               is_primary, created_at, updated_at
        FROM customer_contacts
        WHERE id = ? AND customer_id = ?
        """,
        (contact_id, customer_id),
    ).fetchone()
    return _contact(row) if row is not None else None


def normalize_primary_contact(
    connection: sqlite3.Connection,
    customer_id: int,
    *,
    preferred_contact_id: int | None = None,
) -> int | None:
    rows = connection.execute(
        "SELECT id, is_primary FROM customer_contacts WHERE customer_id = ? ORDER BY id",
        (customer_id,),
    ).fetchall()
    if not rows:
        return None
    contact_ids = [int(row["id"]) for row in rows]
    current_primary_ids = [int(row["id"]) for row in rows if bool(row["is_primary"])]
    if preferred_contact_id in contact_ids:
        primary_id = int(preferred_contact_id)
    elif current_primary_ids:
        primary_id = current_primary_ids[0]
    else:
        primary_id = contact_ids[0]
    if current_primary_ids != [primary_id]:
        connection.execute(
            """
            UPDATE customer_contacts
            SET is_primary = CASE WHEN id = ? THEN 1 ELSE 0 END,
                updated_at = ?
            WHERE customer_id = ?
            """,
            (primary_id, now_text(), customer_id),
        )
    return primary_id


def insert_contact(
    connection: sqlite3.Connection,
    customer_id: int,
    *,
    name: str,
    title: str,
    role: str,
    phone: str,
    email: str,
    wechat: str,
    is_primary: bool,
) -> CustomerContact:
    timestamp = now_text()
    cursor = connection.execute(
        """
        INSERT INTO customer_contacts (
          customer_id, name, title, role, phone, email, wechat,
          is_primary, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (customer_id, name, title, role, phone, email, wechat, int(is_primary), timestamp, timestamp),
    )
    contact = get_contact(connection, customer_id, int(cursor.lastrowid))
    assert contact is not None
    return contact


def update_contact(
    connection: sqlite3.Connection,
    customer_id: int,
    contact_id: int,
    *,
    name: str,
    title: str,
    role: str,
    phone: str,
    email: str,
    wechat: str,
    is_primary: bool,
) -> CustomerContact | None:
    connection.execute(
        """
        UPDATE customer_contacts
        SET name = ?, title = ?, role = ?, phone = ?, email = ?, wechat = ?,
            is_primary = ?, updated_at = ?
        WHERE id = ? AND customer_id = ?
        """,
        (name, title, role, phone, email, wechat, int(is_primary), now_text(), contact_id, customer_id),
    )
    return get_contact(connection, customer_id, contact_id)


def delete_contact(connection: sqlite3.Connection, customer_id: int, contact_id: int) -> int:
    cursor = connection.execute(
        "DELETE FROM customer_contacts WHERE id = ? AND customer_id = ?",
        (contact_id, customer_id),
    )
    return cursor.rowcount
