from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from .platform.api_principal import LEGACY_COMPATIBILITY_SCOPES
from .platform.permissions import (
    ADMIN_ROLE_KEY,
    LEGACY_ROLE_DESCRIPTIONS,
    LEGACY_ROLE_LABELS,
    LEGACY_ROLE_PERMISSIONS,
)
from .platform.sync_identity import MATERIAL_IDENTITY_FIELDS, QUOTE_MATCH_FIELDS, material_key, quote_match_key, stable_sync_id


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
    if "price" in quote_columns:
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


def _add_tube_items(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tube_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL UNIQUE,
          tube_type TEXT NOT NULL DEFAULT '普通管',
          spec_text TEXT NOT NULL DEFAULT '',
          weight_kg REAL,
          tolerance_mm REAL,
          consumption_mm REAL,
          outer_diameter_mm REAL,
          inner_diameter_mm REAL,
          blank_length_text TEXT DEFAULT '',
          inner_diameter_tolerance TEXT DEFAULT '',
          purchase_base INTEGER NOT NULL DEFAULT 1,
          borrowed_from TEXT DEFAULT '',
          note TEXT DEFAULT '',
          source_sheet TEXT DEFAULT '',
          source_row INTEGER,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tube_items_type_active ON tube_items(tube_type, active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tube_items_borrowed_from ON tube_items(borrowed_from)")


def _add_tube_dimensions(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tube_items)")}
    if "outer_diameter_mm" not in columns:
        conn.execute("ALTER TABLE tube_items ADD COLUMN outer_diameter_mm REAL")
    if "inner_diameter_mm" not in columns:
        conn.execute("ALTER TABLE tube_items ADD COLUMN inner_diameter_mm REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tube_items_diameters ON tube_items(outer_diameter_mm, inner_diameter_mm)")


def _add_tube_manufacturing_fields(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tube_items)")}
    if "blank_length_text" not in columns:
        conn.execute("ALTER TABLE tube_items ADD COLUMN blank_length_text TEXT DEFAULT ''")
    if "inner_diameter_tolerance" not in columns:
        conn.execute("ALTER TABLE tube_items ADD COLUMN inner_diameter_tolerance TEXT DEFAULT ''")
    if "purchase_base" not in columns:
        conn.execute("ALTER TABLE tube_items ADD COLUMN purchase_base INTEGER NOT NULL DEFAULT 1")


def _flatten_tube_borrowing(conn: sqlite3.Connection) -> None:
    rows = {
        str(row["code"]): str(row["borrowed_from"] or "")
        for row in conn.execute("SELECT code, borrowed_from FROM tube_items")
    }
    for code, borrowed_from in rows.items():
        if not borrowed_from:
            continue
        visited = {code}
        current = borrowed_from
        while current and current in rows and rows[current]:
            if current in visited:
                current = ""
                break
            visited.add(current)
            current = rows[current]
        if current and current in rows and current != borrowed_from:
            conn.execute("UPDATE tube_items SET borrowed_from = ? WHERE code = ?", (current, code))


def _assign_cross_device_sync_keys(conn: sqlite3.Connection, *, reset: bool) -> None:
    for table in ("quote_records", "material_items"):
        if _columns(conn, table) and "sync_id" not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN sync_id TEXT DEFAULT ''")
    for table, identity_fields, key_factory, namespace in (
        ("quote_records", QUOTE_MATCH_FIELDS, quote_match_key, "quote"),
        ("material_items", MATERIAL_IDENTITY_FIELDS, material_key, "material"),
    ):
        table_columns = _columns(conn, table)
        if not table_columns:
            continue
        if reset:
            conn.execute(f"UPDATE {table} SET sync_id = 'rekey-' || id")
        available_identity_fields = tuple(column for column in identity_fields if column in table_columns)
        condition = "ORDER BY id" if reset else "WHERE COALESCE(sync_id, '') = '' ORDER BY id"
        rows = conn.execute(
            f"SELECT id, {', '.join(available_identity_fields)} FROM {table} {condition}"
        ).fetchall()
        ordinals: dict[str, int] = {}
        for row in rows:
            key = key_factory(dict(row))
            ordinal = ordinals.get(key, 0) + 1
            ordinals[key] = ordinal
            sync_id = stable_sync_id(namespace, key, ordinal)
            conn.execute(f"UPDATE {table} SET sync_id = ? WHERE id = ?", (sync_id, row["id"]))
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_sync_id ON {table}(sync_id)")


def _add_cross_device_sync_keys(conn: sqlite3.Connection) -> None:
    _assign_cross_device_sync_keys(conn, reset=False)


def _rekey_cross_device_sync_keys(conn: sqlite3.Connection) -> None:
    _assign_cross_device_sync_keys(conn, reset=True)


def _drop_quote_record_price(conn: sqlite3.Connection) -> None:
    if "price" in _columns(conn, "quote_records"):
        conn.execute("ALTER TABLE quote_records DROP COLUMN price")


def _quote_no_day(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[2:8] if len(digits) >= 8 else "000000"


def _add_quote_record_quote_no(conn: sqlite3.Connection) -> None:
    if "quote_no" not in _columns(conn, "quote_records"):
        conn.execute("ALTER TABLE quote_records ADD COLUMN quote_no TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quote_no_counters (
          day TEXT PRIMARY KEY,
          last_seq INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quote_records_quote_no ON quote_records(quote_no)"
    )
    rows = conn.execute(
        "SELECT id, quote_date, created_at FROM quote_records WHERE quote_no = '' ORDER BY quote_date, id"
    ).fetchall()
    sequences: dict[str, int] = {}
    for row in rows:
        day = _quote_no_day(row["quote_date"] or row["created_at"])
        sequences[day] = sequences.get(day, 0) + 1
        conn.execute(
            "UPDATE quote_records SET quote_no = ? WHERE id = ?",
            (f"Q{day}{sequences[day]:03d}", row["id"]),
        )
    for day, last_seq in sequences.items():
        conn.execute(
            """
            INSERT INTO quote_no_counters (day, last_seq) VALUES (?, ?)
            ON CONFLICT(day) DO UPDATE SET last_seq = MAX(last_seq, excluded.last_seq)
            """,
            (day, last_seq),
        )


def _add_product_option_values(conn: sqlite3.Connection) -> None:
    # 延迟导入：app.database -> app.migrations 在模块加载期不能反向依赖业务模块。
    from .modules.products.option_values import register_product_option_values

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS product_option_values (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          value TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          UNIQUE(kind, value)
        )
        """
    )
    if not _columns(conn, "products"):
        return
    product_columns = _columns(conn, "products")
    status_column = "product_status" if "product_status" in product_columns else "'' AS product_status"
    rows = conn.execute(f"SELECT series, item, {status_column} FROM products").fetchall()
    for row in rows:
        register_product_option_values(
            conn,
            series=row["series"],
            item=row["item"],
            product_status=row["product_status"],
        )


def _add_customers(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          sync_id TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_name ON customers(name COLLATE NOCASE)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_sync_id ON customers(sync_id)")
    if not _columns(conn, "quote_records"):
        return
    rows = conn.execute(
        "SELECT DISTINCT customer_name FROM quote_records WHERE COALESCE(customer_name, '') <> '' ORDER BY customer_name COLLATE NOCASE"
    ).fetchall()
    for row in rows:
        name = " ".join(str(row["customer_name"]).split())
        if not name:
            continue
        sync_id = stable_sync_id("customer", name.upper(), 1)
        conn.execute("INSERT OR IGNORE INTO customers (name, sync_id) VALUES (?, ?)", (name, sync_id))


def _backfill_quote_customer_ids(conn: sqlite3.Connection) -> None:
    if "customer_id" not in _columns(conn, "quote_records"):
        return
    customers = conn.execute("SELECT id, name FROM customers ORDER BY id").fetchall()
    customers_by_id = {int(row["id"]): str(row["name"]) for row in customers}
    normalized: dict[str, list[tuple[int, str]]] = {}
    for row in customers:
        canonical_name = " ".join(str(row["name"]).split())
        normalized.setdefault(canonical_name.casefold(), []).append(
            (int(row["id"]), str(row["name"]))
        )

    rows = conn.execute(
        "SELECT id, customer_id, customer_name FROM quote_records ORDER BY id"
    ).fetchall()
    for row in rows:
        customer_id = int(row["customer_id"]) if row["customer_id"] is not None else None
        canonical_name = customers_by_id.get(customer_id) if customer_id is not None else None
        if canonical_name is None:
            lookup_name = " ".join(str(row["customer_name"] or "").split())
            matches = normalized.get(lookup_name.casefold(), [])
            if len(matches) != 1:
                continue
            customer_id, canonical_name = matches[0]
        if row["customer_id"] != customer_id or str(row["customer_name"]) != canonical_name:
            conn.execute(
                "UPDATE quote_records SET customer_id = ?, customer_name = ? WHERE id = ?",
                (customer_id, canonical_name, int(row["id"])),
            )


def _add_customer_profiles_documents_and_quote_contracts(conn: sqlite3.Connection) -> None:
    _add_customers(conn)
    customer_columns = _columns(conn, "customers")
    if "code" not in customer_columns:
        conn.execute("ALTER TABLE customers ADD COLUMN code TEXT NOT NULL DEFAULT ''")
    if "status" not in customer_columns:
        conn.execute("ALTER TABLE customers ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if "owner_username" not in customer_columns:
        conn.execute("ALTER TABLE customers ADD COLUMN owner_username TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE customers SET status = 'active' WHERE COALESCE(status, '') = ''")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_code "
        "ON customers(code COLLATE NOCASE) WHERE code <> ''"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_status_owner ON customers(status, owner_username)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_contacts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          customer_id INTEGER NOT NULL REFERENCES customers(id),
          name TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          role TEXT NOT NULL DEFAULT '',
          phone TEXT NOT NULL DEFAULT '',
          email TEXT NOT NULL DEFAULT '',
          wechat TEXT NOT NULL DEFAULT '',
          is_primary INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_contacts_customer ON customer_contacts(customer_id, is_primary DESC, id)"
    )

    if _columns(conn, "quote_records") and "customer_id" not in _columns(conn, "quote_records"):
        conn.execute("ALTER TABLE quote_records ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
    if "customer_id" in _columns(conn, "quote_records"):
        _backfill_quote_customer_ids(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_records_customer_id ON quote_records(customer_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_document_groups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          customer_id INTEGER NOT NULL REFERENCES customers(id),
          sync_id TEXT NOT NULL UNIQUE,
          category TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          language TEXT NOT NULL DEFAULT 'zh-CN',
          current_version INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0,
          created_by TEXT NOT NULL DEFAULT '',
          updated_by TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_document_groups_customer ON customer_document_groups(customer_id, archived, updated_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_document_files (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          group_id INTEGER NOT NULL REFERENCES customer_document_groups(id),
          sync_id TEXT NOT NULL UNIQUE,
          version_no INTEGER NOT NULL,
          original_name TEXT NOT NULL,
          storage_path TEXT NOT NULL UNIQUE,
          content_type TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          created_by TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_document_files_version ON customer_document_files(group_id, version_no, id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contract_documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          contract_type TEXT NOT NULL DEFAULT 'sales',
          contract_no TEXT NOT NULL,
          customer_id INTEGER REFERENCES customers(id),
          customer_name TEXT NOT NULL,
          source_quote_no TEXT NOT NULL DEFAULT '',
          language TEXT NOT NULL DEFAULT 'zh-CN',
          currency TEXT NOT NULL DEFAULT 'CNY',
          source_snapshot_json TEXT NOT NULL DEFAULT '',
          source_snapshot_sha256 TEXT NOT NULL DEFAULT '',
          file_path TEXT NOT NULL UNIQUE,
          created_by TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contract_documents_customer ON contract_documents(customer_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contract_documents_quote_no ON contract_documents(source_quote_no, created_at)"
    )


def _enforce_case_insensitive_customer_codes(conn: sqlite3.Connection) -> None:
    if not {"id", "code"}.issubset(_columns(conn, "customers")):
        return
    # 早期 028 的唯一索引区分大小写，可能同时存在 ABC 与 abc。
    # 客户编号可为空，因此确定性保留最小 id 的编号并清空后续冲突项。
    conn.execute(
        """
        UPDATE customers
        SET code = ''
        WHERE COALESCE(code, '') <> ''
          AND EXISTS (
            SELECT 1
            FROM customers AS retained
            WHERE retained.id < customers.id
              AND retained.code = customers.code COLLATE NOCASE
          )
        """
    )
    conn.execute("DROP INDEX IF EXISTS idx_customers_code")
    conn.execute(
        "CREATE UNIQUE INDEX idx_customers_code "
        "ON customers(code COLLATE NOCASE) WHERE code <> ''"
    )


def _normalize_customer_primary_contacts(conn: sqlite3.Connection) -> None:
    if not {"id", "customer_id", "is_primary"}.issubset(
        _columns(conn, "customer_contacts")
    ):
        return
    preferred_rows = conn.execute(
        """
        SELECT customer_id,
               COALESCE(
                 MIN(CASE WHEN is_primary <> 0 THEN id END),
                 MIN(id)
               ) AS primary_id
        FROM customer_contacts
        GROUP BY customer_id
        ORDER BY customer_id
        """
    ).fetchall()
    for row in preferred_rows:
        conn.execute(
            """
            UPDATE customer_contacts
            SET is_primary = CASE WHEN id = ? THEN 1 ELSE 0 END
            WHERE customer_id = ?
            """,
            (int(row["primary_id"]), int(row["customer_id"])),
        )


def _finalize_customer_workspace_integrity(conn: sqlite3.Connection) -> None:
    contract_columns = _columns(conn, "contract_documents")
    if "source_snapshot_json" not in contract_columns:
        conn.execute(
            "ALTER TABLE contract_documents "
            "ADD COLUMN source_snapshot_json TEXT NOT NULL DEFAULT ''"
        )
    if "source_snapshot_sha256" not in contract_columns:
        conn.execute(
            "ALTER TABLE contract_documents "
            "ADD COLUMN source_snapshot_sha256 TEXT NOT NULL DEFAULT ''"
        )

    _enforce_case_insensitive_customer_codes(conn)
    _normalize_customer_primary_contacts(conn)
    _backfill_quote_customer_ids(conn)


def _repair_customer_workspace_integrity(conn: sqlite3.Connection) -> None:
    # 030 会在已记录 029 的数据库上顺序执行，确保新加入的数据修复不会
    # 依赖重跑已应用 migration。
    _enforce_case_insensitive_customer_codes(conn)
    _normalize_customer_primary_contacts(conn)
    _backfill_quote_customer_ids(conn)


def _add_editable_roles_and_user_permission_overrides(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS roles (
          role_key TEXT PRIMARY KEY,
          name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          description TEXT NOT NULL DEFAULT '',
          is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0, 1)),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS role_permissions (
          role_key TEXT NOT NULL,
          permission TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (role_key, permission),
          FOREIGN KEY (role_key) REFERENCES roles(role_key) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_permission_overrides (
          user_id INTEGER NOT NULL,
          permission TEXT NOT NULL,
          effect TEXT NOT NULL CHECK (effect IN ('allow', 'deny')),
          updated_at TEXT NOT NULL,
          PRIMARY KEY (user_id, permission),
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    user_columns = _columns(conn, "users")
    if "role" in user_columns:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
    timestamp = conn.execute("SELECT datetime('now','localtime')").fetchone()[0]
    for role_key, name in LEGACY_ROLE_LABELS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO roles (
              role_key, name, description, is_system, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                role_key,
                name,
                LEGACY_ROLE_DESCRIPTIONS[role_key],
                1 if role_key == ADMIN_ROLE_KEY else 0,
                timestamp,
                timestamp,
            ),
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO role_permissions (role_key, permission, created_at)
            VALUES (?, ?, ?)
            """,
            (
                (role_key, permission, timestamp)
                for permission in sorted(LEGACY_ROLE_PERMISSIONS[role_key])
            ),
        )
    existing_role_keys = (
        {
            str(row["role"])
            for row in conn.execute("SELECT DISTINCT role FROM users ORDER BY role").fetchall()
        }
        if "role" in user_columns
        else set()
    )
    for role_key in sorted(existing_role_keys - set(LEGACY_ROLE_LABELS)):
        if conn.execute(
            "SELECT 1 FROM roles WHERE role_key = ?",
            (role_key,),
        ).fetchone():
            continue
        base_name = role_key.strip() or "历史角色"
        candidate = base_name
        suffix = 2
        while conn.execute(
            "SELECT 1 FROM roles WHERE name = ? COLLATE NOCASE",
            (candidate,),
        ).fetchone():
            candidate = f"{base_name} ({suffix})"
            suffix += 1
        conn.execute(
            """
            INSERT INTO roles (role_key, name, description, is_system, created_at, updated_at)
            VALUES (?, ?, '从历史账号记录迁移的角色。', 0, ?, ?)
            """,
            (role_key, candidate, timestamp, timestamp),
        )


def _grant_view_product_prices_to_existing_roles(conn: sqlite3.Connection) -> None:
    # 新权限上线前所有角色都能看到产品单价。为保持现状，给所有现有非系统
    # 角色补齐该权限，是否收回由管理员在角色管理中决定。
    timestamp = conn.execute("SELECT datetime('now','localtime')").fetchone()[0]
    conn.execute(
        """
        INSERT OR IGNORE INTO role_permissions (role_key, permission, created_at)
        SELECT role_key, 'view_product_prices', ? FROM roles WHERE is_system = 0
        """,
        (timestamp,),
    )


def _revoke_view_product_prices_permission(conn: sqlite3.Connection) -> None:
    # view_product_prices 独立权限已撤回：目录价可见性改由 manage_customer_prices
    # 统一控制。清理 032 及手工勾选留下的授权行，避免孤儿数据。
    conn.execute("DELETE FROM role_permissions WHERE permission = 'view_product_prices'")
    conn.execute("DELETE FROM user_permission_overrides WHERE permission = 'view_product_prices'")


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
    ("018_tube_items", _add_tube_items),
    ("019_tube_dimensions", _add_tube_dimensions),
    ("020_tube_manufacturing_fields", _add_tube_manufacturing_fields),
    ("021_flatten_tube_borrowing", _flatten_tube_borrowing),
    ("022_cross_device_sync_keys", _add_cross_device_sync_keys),
    ("023_rekey_cross_device_sync_keys", _rekey_cross_device_sync_keys),
    ("024_drop_quote_record_price", _drop_quote_record_price),
    ("025_create_product_option_values", _add_product_option_values),
    ("026_quote_record_quote_no", _add_quote_record_quote_no),
    ("027_customers", _add_customers),
    ("028_customer_profiles_documents_and_quote_contracts", _add_customer_profiles_documents_and_quote_contracts),
    ("029_customer_workspace_integrity", _finalize_customer_workspace_integrity),
    ("030_repair_customer_workspace_integrity", _repair_customer_workspace_integrity),
    ("031_editable_roles_and_user_permission_overrides", _add_editable_roles_and_user_permission_overrides),
    ("032_grant_view_product_prices", _grant_view_product_prices_to_existing_roles),
    ("033_revoke_view_product_prices", _revoke_view_product_prices_permission),
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
