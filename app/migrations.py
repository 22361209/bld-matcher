from __future__ import annotations

import sqlite3
from collections.abc import Callable


Migration = tuple[str, Callable[[sqlite3.Connection], None]]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_audit_actor(conn: sqlite3.Connection) -> None:
    if "actor" not in _columns(conn, "audit_logs"):
        conn.execute("ALTER TABLE audit_logs ADD COLUMN actor TEXT DEFAULT ''")


def _add_product_price_and_image(conn: sqlite3.Connection) -> None:
    product_columns = _columns(conn, "products")
    if "price_cny" not in product_columns:
        conn.execute("ALTER TABLE products ADD COLUMN price_cny REAL")
    if "image_path" not in product_columns:
        conn.execute("ALTER TABLE products ADD COLUMN image_path TEXT DEFAULT ''")


def _add_product_drawings(conn: sqlite3.Connection) -> None:
    product_columns = _columns(conn, "products")
    if "drawing_path" not in product_columns:
        conn.execute("ALTER TABLE products ADD COLUMN drawing_path TEXT DEFAULT ''")
    if "drawing_original_name" not in product_columns:
        conn.execute("ALTER TABLE products ADD COLUMN drawing_original_name TEXT DEFAULT ''")
    if "drawing_updated_at" not in product_columns:
        conn.execute("ALTER TABLE products ADD COLUMN drawing_updated_at TEXT DEFAULT ''")


def _add_product_image_slots(conn: sqlite3.Connection) -> None:
    product_columns = _columns(conn, "products")
    for index in range(2, 6):
        field = f"image_path_{index}"
        if field not in product_columns:
            conn.execute(f"ALTER TABLE products ADD COLUMN {field} TEXT DEFAULT ''")


def _add_internal_api_keys(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS internal_api_keys (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL DEFAULT 'OpenClaw',
          token_hash TEXT NOT NULL UNIQUE,
          token_prefix TEXT DEFAULT '',
          token_suffix TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          created_by TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_used_at TEXT DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_internal_api_keys_active ON internal_api_keys(active)")


MIGRATIONS: tuple[Migration, ...] = (
    ("001_audit_log_actor", _add_audit_actor),
    ("002_product_price_and_image", _add_product_price_and_image),
    ("003_product_drawings", _add_product_drawings),
    ("004_product_image_slots", _add_product_image_slots),
    ("005_internal_api_keys", _add_internal_api_keys),
)


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          id TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {row["id"] for row in conn.execute("SELECT id FROM schema_migrations").fetchall()}
    for migration_id, migration in MIGRATIONS:
        if migration_id in applied:
            continue
        migration(conn)
        conn.execute("INSERT INTO schema_migrations (id) VALUES (?)", (migration_id,))
    conn.commit()
