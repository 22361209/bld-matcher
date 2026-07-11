from __future__ import annotations

import sqlite3
import json
from collections.abc import Callable
from datetime import datetime, timedelta

from .platform.api_principal import LEGACY_COMPATIBILITY_SCOPES


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


def _add_internal_api_key_plaintext(conn: sqlite3.Connection) -> None:
    # 保留历史 migration id，旧版已执行的数据库由 012 清理该列。
    return None


def _scrub_internal_api_key_plaintext(conn: sqlite3.Connection) -> None:
    if "token_plain" in _columns(conn, "internal_api_keys"):
        conn.execute("UPDATE internal_api_keys SET token_plain = '' WHERE COALESCE(token_plain, '') != ''")
        conn.execute("ALTER TABLE internal_api_keys DROP COLUMN token_plain")


def _add_api_platform_tables(conn: sqlite3.Connection) -> None:
    key_columns = _columns(conn, "internal_api_keys")
    if "scopes" not in key_columns:
        conn.execute("ALTER TABLE internal_api_keys ADD COLUMN scopes TEXT NOT NULL DEFAULT '[]'")
    if "expires_at" not in key_columns:
        conn.execute("ALTER TABLE internal_api_keys ADD COLUMN expires_at TEXT DEFAULT ''")
    conn.execute(
        "UPDATE internal_api_keys SET scopes = ? WHERE scopes IS NULL OR scopes = '' OR scopes = '[]'",
        (json.dumps(sorted(LEGACY_COMPATIBILITY_SCOPES)),),
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_idempotency_expires ON api_idempotency_keys(expires_at)")


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
          version INTEGER NOT NULL DEFAULT 1,
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


def _add_quote_record_version(conn: sqlite3.Connection) -> None:
    if "version" not in _columns(conn, "quote_records"):
        conn.execute("ALTER TABLE quote_records ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    conn.execute("UPDATE quote_records SET version = 1 WHERE version IS NULL OR version < 1")


def _add_idempotency_response_headers(conn: sqlite3.Connection) -> None:
    if "response_headers" not in _columns(conn, "api_idempotency_keys"):
        conn.execute("ALTER TABLE api_idempotency_keys ADD COLUMN response_headers TEXT DEFAULT '{}'")


def _add_api_artifacts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
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
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_artifacts_owner ON api_artifacts(owner_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_artifacts_expires ON api_artifacts(expires_at)")


def _add_runtime_platform_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS background_jobs (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          request_payload TEXT NOT NULL DEFAULT '{}',
          progress_payload TEXT NOT NULL DEFAULT '{}',
          result_payload TEXT NOT NULL DEFAULT '{}',
          error_code TEXT NOT NULL DEFAULT '',
          error_message TEXT NOT NULL DEFAULT '',
          cancel_requested INTEGER NOT NULL DEFAULT 0,
          attempt INTEGER NOT NULL DEFAULT 0,
          max_attempts INTEGER NOT NULL DEFAULT 3,
          run_after TEXT NOT NULL,
          lease_owner TEXT NOT NULL DEFAULT '',
          lease_expires_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          started_at TEXT NOT NULL DEFAULT '',
          finished_at TEXT NOT NULL DEFAULT '',
          expires_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_background_jobs_owner ON background_jobs(owner_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_background_jobs_claim ON background_jobs(status, run_after, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_background_jobs_expiry ON background_jobs(expires_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS background_job_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          FOREIGN KEY (job_id) REFERENCES background_jobs(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_background_job_events_job ON background_job_events(job_id, id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_provider_calls (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL DEFAULT '',
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          data_type TEXT NOT NULL,
          caller TEXT NOT NULL,
          status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 1,
          latency_ms INTEGER NOT NULL DEFAULT 0,
          prompt_tokens INTEGER NOT NULL DEFAULT 0,
          completion_tokens INTEGER NOT NULL DEFAULT 0,
          total_tokens INTEGER NOT NULL DEFAULT 0,
          estimated_cost_usd REAL NOT NULL DEFAULT 0,
          error_code TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_provider_calls_job ON ai_provider_calls(job_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_provider_calls_created ON ai_provider_calls(created_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_heartbeats (
          component TEXT NOT NULL,
          instance_id TEXT NOT NULL,
          payload TEXT NOT NULL DEFAULT '{}',
          updated_at TEXT NOT NULL,
          PRIMARY KEY (component, instance_id)
        )
        """
    )

    if "shipment_recognition_jobs" not in {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }:
        return
    rows = conn.execute("SELECT id, owner, payload, created_at, updated_at FROM shipment_recognition_jobs").fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        legacy_status = str(payload.get("status") or "failed")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        if legacy_status == "completed":
            status = "completed"
            error_code = error_message = ""
        else:
            status = "failed"
            error_code = "job.legacy_interrupted"
            error_message = "服务升级前的识别任务已中断，请重新提交。"
        updated_at = str(row["updated_at"] or row["created_at"] or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        try:
            expires_at = (datetime.strptime(updated_at[:19], "%Y-%m-%d %H:%M:%S") + timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            expires_at = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        progress = {
            key: payload[key]
            for key in ("phase", "message", "total", "completed", "percent", "current")
            if key in payload
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO background_jobs (
              id, kind, owner_id, status, request_payload, progress_payload, result_payload,
              error_code, error_message, cancel_requested, attempt, max_attempts, run_after,
              lease_owner, lease_expires_at, created_at, updated_at, started_at, finished_at, expires_at
            ) VALUES (?, 'shipping.recognition', ?, ?, '{}', ?, ?, ?, ?, 0, 1, 1, ?, '', '', ?, ?, '', ?, ?)
            """,
            (
                str(row["id"]),
                str(row["owner"]),
                status,
                json.dumps(progress, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
                error_code,
                error_message,
                updated_at,
                str(row["created_at"] or updated_at),
                updated_at,
                updated_at,
                expires_at,
            ),
        )
    conn.execute("DROP TABLE shipment_recognition_jobs")


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
    ("012_scrub_internal_api_key_plaintext", _scrub_internal_api_key_plaintext),
    ("013_api_principal_scopes_and_idempotency", _add_api_platform_tables),
    ("014_quote_record_version", _add_quote_record_version),
    ("015_idempotency_response_headers", _add_idempotency_response_headers),
    ("016_api_artifacts", _add_api_artifacts),
    ("017_runtime_jobs_ai_and_health", _add_runtime_platform_tables),
)


def run_migrations(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise RuntimeError("数据库迁移必须在独立事务中运行。")
    try:
        # SQLite 的写事务同时承担跨进程迁移锁。拿到锁后重新读取记录，
        # 避免多个 Gunicorn worker 同时执行同一条迁移。
        conn.execute("BEGIN IMMEDIATE")
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
    except Exception:
        conn.rollback()
        raise
