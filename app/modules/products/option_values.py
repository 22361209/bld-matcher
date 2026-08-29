from __future__ import annotations

import sqlite3

from app.product_status import canonical_product_status

from .brand_normalization import canonicalize_brands
from .domain import ProductOptionValue


def _option_value(row: sqlite3.Row) -> ProductOptionValue:
    keys = row.keys() if hasattr(row, "keys") else []
    return ProductOptionValue(
        id=int(row["id"]),
        kind=str(row["kind"]),
        value=str(row["value"]),
        updated_at=str(row["updated_at"] or ""),
        sort_order=int(row["sort_order"]) if "sort_order" in keys else 0,
        usage_count=int(row["usage_count"]) if "usage_count" in keys else 0,
    )


def list_option_values(connection: sqlite3.Connection) -> list[ProductOptionValue]:
    rows = connection.execute(
        """
        SELECT o.id, o.kind, o.value, o.updated_at, o.sort_order,
          CASE WHEN o.kind = 'item' THEN
            (SELECT COUNT(*) FROM products p WHERE p.item = o.value AND p.active = 1)
          ELSE 0 END AS usage_count
        FROM product_option_values o
        ORDER BY o.kind,
          CASE WHEN o.sort_order > 0 THEN 0 ELSE 1 END,
          o.sort_order,
          usage_count DESC,
          o.value COLLATE NOCASE
        """
    ).fetchall()
    return [_option_value(row) for row in rows]


def get_option_value(connection: sqlite3.Connection, option_id: int) -> ProductOptionValue | None:
    row = connection.execute(
        "SELECT id, kind, value, updated_at FROM product_option_values WHERE id = ?",
        (option_id,),
    ).fetchone()
    return _option_value(row) if row is not None else None


def option_value_exists(connection: sqlite3.Connection, kind: str, value: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM product_option_values WHERE kind = ? AND value = ?",
        (kind, value),
    ).fetchone()
    return row is not None


def add_option_value(connection: sqlite3.Connection, kind: str, value: str) -> bool:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO product_option_values (kind, value) VALUES (?, ?)",
        (kind, value),
    )
    return cursor.rowcount > 0


def rename_option_value(connection: sqlite3.Connection, option_id: int, value: str) -> None:
    connection.execute(
        "UPDATE product_option_values SET value = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (value, option_id),
    )


def delete_option_value(connection: sqlite3.Connection, option_id: int) -> None:
    connection.execute("DELETE FROM product_option_values WHERE id = ?", (option_id,))


def write_option_sort_orders(connection: sqlite3.Connection, kind: str, ordered_ids: list[int]) -> None:
    """Persist one kind's display order as 1-based sort_order values."""
    connection.execute(
        "UPDATE product_option_values SET sort_order = 0 WHERE kind = ?",
        (kind,),
    )
    for position, option_id in enumerate(ordered_ids, start=1):
        connection.execute(
            "UPDATE product_option_values SET sort_order = ? WHERE id = ? AND kind = ?",
            (position, option_id, kind),
        )


def normalized_option_values(kind: str, value: object) -> list[str]:
    """Return the canonical stored form(s) for a managed option value."""

    if kind == "brand":
        return [brand for brand in canonicalize_brands(value).split("\n") if brand]
    if kind == "item":
        compacted = " ".join(str(value or "").split())
        return [compacted] if compacted else []
    if kind == "product_status":
        canonical = canonical_product_status(value)
        return [canonical] if canonical else []
    return []


def register_product_option_values(
    connection: sqlite3.Connection,
    *,
    series: object,
    item: object,
    product_status: object,
) -> None:
    """Record the option values carried by a saved product row (idempotent)."""

    candidates: list[tuple[str, str]] = [("brand", brand) for brand in normalized_option_values("brand", series)]
    candidates.extend(("item", name) for name in normalized_option_values("item", item))
    candidates.extend(("product_status", status) for status in normalized_option_values("product_status", product_status))
    seen: set[tuple[str, str]] = set()
    for kind, value in candidates:
        if (kind, value) in seen:
            continue
        seen.add((kind, value))
        add_option_value(connection, kind, value)
