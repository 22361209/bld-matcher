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
          token_plain TEXT DEFAULT '',
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


def _add_internal_api_key_plaintext(conn: sqlite3.Connection) -> None:
    if "token_plain" not in _columns(conn, "internal_api_keys"):
        conn.execute("ALTER TABLE internal_api_keys ADD COLUMN token_plain TEXT DEFAULT ''")


def _add_shipment_recognition_jobs(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shipment_recognition_jobs (
          id TEXT PRIMARY KEY,
          owner TEXT NOT NULL,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shipment_recognition_jobs_owner ON shipment_recognition_jobs(owner)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shipment_recognition_jobs_updated ON shipment_recognition_jobs(updated_at)")


def _add_product_status(conn: sqlite3.Connection) -> None:
    product_columns = _columns(conn, "products")
    if "product_status" not in product_columns:
        conn.execute("ALTER TABLE products ADD COLUMN product_status TEXT DEFAULT ''")


def _add_quote_records(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quote_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          customer_name TEXT NOT NULL,
          bld_no TEXT DEFAULT '',
          customer_product_code TEXT DEFAULT '',
          product_model TEXT NOT NULL,
          price REAL NOT NULL,
          tax_price REAL,
          net_price REAL,
          currency TEXT NOT NULL,
          moq INTEGER,
          quote_date TEXT NOT NULL,
          quoted_by TEXT DEFAULT '',
          source_type TEXT NOT NULL DEFAULT 'manual',
          source_text TEXT DEFAULT '',
          attachment_path TEXT DEFAULT '',
          remark TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_records_customer_model ON quote_records(customer_name, product_model)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_records_customer_bld ON quote_records(customer_name, bld_no)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_records_date ON quote_records(quote_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_records_currency ON quote_records(currency)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_records_quoted_by ON quote_records(quoted_by)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quote_record_revisions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          quote_id INTEGER NOT NULL,
          changed_by TEXT DEFAULT '',
          before_json TEXT NOT NULL,
          after_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (quote_id) REFERENCES quote_records(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_record_revisions_quote ON quote_record_revisions(quote_id)")


def _add_quote_record_bld_prices(conn: sqlite3.Connection) -> None:
    quote_columns = _columns(conn, "quote_records")
    if "bld_no" not in quote_columns:
        conn.execute("ALTER TABLE quote_records ADD COLUMN bld_no TEXT DEFAULT ''")
    if "customer_product_code" not in quote_columns:
        conn.execute("ALTER TABLE quote_records ADD COLUMN customer_product_code TEXT DEFAULT ''")
    if "tax_price" not in quote_columns:
        conn.execute("ALTER TABLE quote_records ADD COLUMN tax_price REAL")
    if "net_price" not in quote_columns:
        conn.execute("ALTER TABLE quote_records ADD COLUMN net_price REAL")
    conn.execute("UPDATE quote_records SET bld_no = product_model WHERE COALESCE(bld_no, '') = ''")
    conn.execute("UPDATE quote_records SET tax_price = price WHERE tax_price IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_records_customer_bld ON quote_records(customer_name, bld_no)")


def _add_customer_price_bld_index(conn: sqlite3.Connection) -> None:
    price_columns = _columns(conn, "customer_price_records")
    if "bld_no" not in price_columns:
        conn.execute("ALTER TABLE customer_price_records ADD COLUMN bld_no TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_price_records_bld ON customer_price_records(bld_no)")


MIGRATIONS: tuple[Migration, ...] = (
    ("001_audit_log_actor", _add_audit_actor),
    ("002_product_price_and_image", _add_product_price_and_image),
    ("003_product_drawings", _add_product_drawings),
    ("004_product_image_slots", _add_product_image_slots),
    ("005_internal_api_keys", _add_internal_api_keys),
    ("006_shipment_recognition_jobs", _add_shipment_recognition_jobs),
    ("007_product_status", _add_product_status),
    ("008_internal_api_key_plaintext", _add_internal_api_key_plaintext),
    ("009_quote_records", _add_quote_records),
    ("010_quote_record_bld_prices", _add_quote_record_bld_prices),
    ("011_customer_price_bld_index", _add_customer_price_bld_index),
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
