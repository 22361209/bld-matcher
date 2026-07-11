from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .bld_sort import compare_bld_no
from .migrations import run_migrations


_INIT_LOCK = threading.Lock()
_INITIALIZED_DB_PATHS: set[Path] = set()


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bld_no TEXT NOT NULL UNIQUE,
  series TEXT DEFAULT '',
  item TEXT DEFAULT '',
  oe_no_1 TEXT DEFAULT '',
  oe_no_2 TEXT DEFAULT '',
  models TEXT DEFAULT '',
  price_cny REAL,
  product_status TEXT DEFAULT '',
  image_path TEXT DEFAULT '',
  image_path_2 TEXT DEFAULT '',
  image_path_3 TEXT DEFAULT '',
  image_path_4 TEXT DEFAULT '',
  image_path_5 TEXT DEFAULT '',
  drawing_path TEXT DEFAULT '',
  drawing_original_name TEXT DEFAULT '',
  drawing_updated_at TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  source TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_code TEXT NOT NULL UNIQUE,
  bld_no TEXT NOT NULL,
  note TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_key TEXT NOT NULL,
  actor TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT DEFAULT '',
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS material_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model TEXT NOT NULL,
  code TEXT DEFAULT '',
  category TEXT DEFAULT '',
  car TEXT DEFAULT '',
  part TEXT DEFAULT '',
  spec_text TEXT DEFAULT '',
  pieces REAL NOT NULL,
  thickness REAL NOT NULL,
  width REAL NOT NULL,
  length REAL NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  source TEXT DEFAULT '',
  source_row INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_price_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  record_type TEXT NOT NULL DEFAULT 'quote',
  customer_name TEXT NOT NULL,
  record_date TEXT NOT NULL,
  document_no TEXT DEFAULT '',
  source_name TEXT DEFAULT '',
  source_code TEXT DEFAULT '',
  oe_no TEXT DEFAULT '',
  bld_no TEXT DEFAULT '',
  item TEXT DEFAULT '',
  models TEXT DEFAULT '',
  price_cny REAL,
  price_usd REAL,
  currency TEXT DEFAULT 'CNY',
  exchange_rate REAL,
  tax_included INTEGER NOT NULL DEFAULT 1,
  note TEXT DEFAULT '',
  source_file TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customer_price_records_customer ON customer_price_records(customer_name);
CREATE INDEX IF NOT EXISTS idx_customer_price_records_date ON customer_price_records(record_date);

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
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quote_records_customer_model ON quote_records(customer_name, product_model);
CREATE INDEX IF NOT EXISTS idx_quote_records_date ON quote_records(quote_date);
CREATE INDEX IF NOT EXISTS idx_quote_records_currency ON quote_records(currency);
CREATE INDEX IF NOT EXISTS idx_quote_records_quoted_by ON quote_records(quoted_by);

CREATE TABLE IF NOT EXISTS quote_record_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  quote_id INTEGER NOT NULL,
  changed_by TEXT DEFAULT '',
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (quote_id) REFERENCES quote_records(id)
);

CREATE INDEX IF NOT EXISTS idx_quote_record_revisions_quote ON quote_record_revisions(quote_id);

CREATE TABLE IF NOT EXISTS internal_api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL DEFAULT 'OpenClaw',
  token_hash TEXT NOT NULL UNIQUE,
  token_prefix TEXT DEFAULT '',
  token_suffix TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  scopes TEXT NOT NULL DEFAULT '[]',
  expires_at TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_internal_api_keys_active ON internal_api_keys(active);

CREATE TABLE IF NOT EXISTS api_idempotency_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  principal_id TEXT NOT NULL,
  method TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  state TEXT NOT NULL,
  response_status INTEGER,
  response_body TEXT DEFAULT '',
  response_content_type TEXT DEFAULT 'application/json',
  response_headers TEXT DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  UNIQUE(principal_id, method, endpoint, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_api_idempotency_expires ON api_idempotency_keys(expires_at);

CREATE TABLE IF NOT EXISTS api_artifacts (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_downloaded_at TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_api_artifacts_owner ON api_artifacts(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_api_artifacts_expires ON api_artifacts(expires_at);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    needs_init = not database_path.exists()
    connection = sqlite3.connect(database_path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.create_collation("BLD_NATURAL", compare_bld_no)
    try:
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
    except sqlite3.DatabaseError:
        pass
    resolved = database_path.resolve()
    if needs_init or resolved not in _INITIALIZED_DB_PATHS:
        with _INIT_LOCK:
            if needs_init or resolved not in _INITIALIZED_DB_PATHS:
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                except sqlite3.DatabaseError:
                    pass
                connection.executescript(SCHEMA)
                run_migrations(connection)
                _INITIALIZED_DB_PATHS.add(resolved)
    return connection
