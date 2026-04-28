from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from werkzeug.security import generate_password_hash

from .matcher import ProductCatalog, compact_text, normalize_code, split_codes


PASSWORD_HASH_METHOD = "pbkdf2:sha256"


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=PASSWORD_HASH_METHOD)


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
  image_path TEXT DEFAULT '',
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
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _ensure_audit_actor_column(conn)
    _ensure_product_extra_columns(conn)
    return conn


def _ensure_audit_actor_column(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(audit_logs)").fetchall()]
    if "actor" not in columns:
        conn.execute("ALTER TABLE audit_logs ADD COLUMN actor TEXT DEFAULT ''")
        conn.commit()


def _ensure_product_extra_columns(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()]
    if "price_cny" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN price_cny REAL")
    if "image_path" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN image_path TEXT DEFAULT ''")
    conn.commit()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean_multiline(value: object) -> str:
    text = "" if value is None else str(value)
    lines = [compact_text(line) for line in text.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def clean_oe_list(value: object) -> str:
    return "\n".join(split_codes(value))


def merge_unique_lines(values: list[object], *, oe: bool = False) -> str:
    seen: list[str] = []
    for value in values:
        text = clean_oe_list(value) if oe else clean_multiline(value)
        for line in text.split("\n"):
            if line and line not in seen:
                seen.append(line)
    return "\n".join(seen)


def _parse_price(value: object) -> float | None:
    text = compact_text(value)
    if not text:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def _parse_required_float(value: object, label: str) -> float:
    text = compact_text(value)
    if not text:
        raise ValueError(f"{label}不能为空。")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{label}必须是数字：{text}") from exc


def log_event(conn: sqlite3.Connection, action: str, target_type: str, target_key: str, detail: str = "", actor: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_logs (action, target_type, target_key, actor, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (action, target_type, target_key, actor, detail, now_text()),
    )


def _field_changes(before: sqlite3.Row | None, after: dict) -> list[str]:
    labels = {
        "series": "品牌",
        "item": "产品名称",
        "oe_no_1": "OE 号",
        "oe_no_2": "品牌号码",
        "models": "车型",
        "price_cny": "含税单价",
        "image_path": "产品图片",
        "active": "状态",
    }
    if before is None:
        return ["新增产品"]
    changes = []
    for field, label in labels.items():
        old = str(before[field] or "")
        new = str(after[field] or "")
        if old != new:
            changes.append(f"{label}: {old or '(空)'} -> {new or '(空)'}")
    return changes


def upsert_product(conn: sqlite3.Connection, data: dict, source: str = "manual", audit: bool = True, actor: str = "") -> None:
    timestamp = now_text()
    bld_no = compact_text(data.get("bld_no") or data.get("BLD NO."))
    if not bld_no:
        raise ValueError("BLD NO. 不能为空。")

    values = {
        "bld_no": bld_no,
        "series": clean_multiline(data.get("series") or data.get("SERIES")),
        "item": clean_multiline(data.get("item") or data.get("ITEM")),
        "oe_no_1": clean_oe_list(data.get("oe_no_1") or data.get("OE NO.1")),
        "oe_no_2": clean_oe_list(data.get("oe_no_2") or data.get("OE NO.2")),
        "models": clean_multiline(data.get("models") or data.get("Models")),
        "price_cny": _parse_price(data.get("price_cny")),
        "image_path": compact_text(data.get("image_path")),
        "active": 1 if str(data.get("active", "1")) != "0" else 0,
        "source": source,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    before = conn.execute("SELECT * FROM products WHERE bld_no = ?", (bld_no,)).fetchone()
    conn.execute(
        """
        INSERT INTO products
          (bld_no, series, item, oe_no_1, oe_no_2, models, price_cny, image_path, active, source, created_at, updated_at)
        VALUES
          (:bld_no, :series, :item, :oe_no_1, :oe_no_2, :models, :price_cny, :image_path, :active, :source, :created_at, :updated_at)
        ON CONFLICT(bld_no) DO UPDATE SET
          series=excluded.series,
          item=excluded.item,
          oe_no_1=excluded.oe_no_1,
          oe_no_2=excluded.oe_no_2,
          models=excluded.models,
          price_cny=COALESCE(excluded.price_cny, products.price_cny),
          image_path=CASE WHEN excluded.image_path != '' THEN excluded.image_path ELSE products.image_path END,
          active=excluded.active,
          source=excluded.source,
          updated_at=excluded.updated_at
        """,
        values,
    )
    if audit:
        changes = _field_changes(before, values)
        if changes:
            action = "新增产品" if before is None else "编辑产品"
            log_event(conn, action, "product", bld_no, "\n".join(changes[:20]), actor=actor)
    conn.commit()


def import_catalog(conn: sqlite3.Connection, catalog_path: Path, replace: bool = False, actor: str = "") -> int:
    catalog = ProductCatalog.from_excel(catalog_path)
    if replace:
        conn.execute("DELETE FROM products")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in catalog.rows:
        bld_no = compact_text(row.get("BLD NO."))
        if bld_no:
            grouped[bld_no].append(row)

    for bld_no, rows in grouped.items():
        merged = {
            "BLD NO.": bld_no,
            "SERIES": merge_unique_lines([row.get("SERIES") for row in rows]),
            "ITEM": merge_unique_lines([row.get("ITEM") for row in rows]),
            "OE NO.1": merge_unique_lines([row.get("OE NO.1") for row in rows], oe=True),
            "OE NO.2": merge_unique_lines([row.get("OE NO.2") for row in rows], oe=True),
            "Models": merge_unique_lines([row.get("Models") for row in rows]),
            "price_cny": None,
            "image_path": "",
        }
        upsert_product(conn, merged, source=catalog_path.name, audit=False)
    log_event(conn, "导入目录", "catalog", catalog_path.name, f"汇总导入 {len(grouped)} 个唯一 BLD 号", actor=actor)
    conn.commit()
    return len(grouped)


def bootstrap_from_excel(db_path: Path, catalog_path: Path) -> None:
    with connect(db_path) as conn:
        existing = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if existing == 0 and catalog_path.exists():
            import_catalog(conn, catalog_path, replace=True)


def list_products(
    conn: sqlite3.Connection,
    query: str = "",
    include_inactive: bool = False,
    only_inactive: bool = False,
    limit: int = 3000,
    bld_query: str = "",
    oe_query: str = "",
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM products"
    params: list[object] = []
    clauses = []
    if only_inactive:
        clauses.append("active = 0")
    elif not include_inactive:
        clauses.append("active = 1")
    if query.strip() and not bld_query.strip() and not oe_query.strip():
        bld_query = query
    if bld_query.strip():
        clauses.append("UPPER(bld_no) LIKE ?")
        params.append(f"%{bld_query.strip().upper()}%")
    if oe_query.strip():
        norm_key = f"%{normalize_code(oe_query)}%"
        clauses.append(
            "(REPLACE(REPLACE(REPLACE(REPLACE(UPPER(oe_no_1), '-', ''), ' ', ''), CHAR(10), '|'), CHAR(13), '|') LIKE ? "
            "OR REPLACE(REPLACE(REPLACE(REPLACE(UPPER(oe_no_2), '-', ''), ' ', ''), CHAR(10), '|'), CHAR(13), '|') LIKE ?)"
        )
        params.extend([norm_key, norm_key])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY bld_no LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_product(conn: sqlite3.Connection, product_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def deactivate_product(conn: sqlite3.Connection, product_id: int, actor: str = "") -> None:
    row = get_product(conn, product_id)
    conn.execute("UPDATE products SET active = 0, updated_at = ? WHERE id = ?", (now_text(), product_id))
    if row:
        log_event(conn, "停用产品", "product", row["bld_no"], "状态: 启用 -> 停用", actor=actor)
    conn.commit()


def product_stats(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(active = 1) AS active, SUM(active = 0) AS inactive FROM products"
    ).fetchone()
    alias_count = conn.execute("SELECT COUNT(*) FROM aliases WHERE active = 1").fetchone()[0]
    return {
        "products": row["total"] or 0,
        "active": row["active"] or 0,
        "inactive": row["inactive"] or 0,
        "aliases": alias_count or 0,
    }


def save_alias(conn: sqlite3.Connection, source_code: str, bld_no: str, note: str = "", actor: str = "") -> None:
    timestamp = now_text()
    key = normalize_code(source_code)
    before = conn.execute("SELECT * FROM aliases WHERE source_code = ?", (key,)).fetchone()
    conn.execute(
        """
        INSERT INTO aliases (source_code, bld_no, note, active, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(source_code) DO UPDATE SET
          bld_no=excluded.bld_no,
          note=excluded.note,
          active=1,
          updated_at=excluded.updated_at
        """,
        (key, compact_text(bld_no), compact_text(note), timestamp, timestamp),
    )
    action = "新增人工映射" if before is None else "编辑人工映射"
    log_event(conn, action, "alias", key, f"{key} -> {compact_text(bld_no)}", actor=actor)
    conn.commit()


def append_product_code(conn: sqlite3.Connection, bld_no: str, code_value: str, target: str = "oe", actor: str = "") -> bool:
    product = conn.execute("SELECT * FROM products WHERE bld_no = ?", (compact_text(bld_no),)).fetchone()
    if not product:
        return False

    code = compact_text(code_value)
    if not code:
        return False
    field = "oe_no_2" if target == "brand_code" else "oe_no_1"
    label = "品牌号码" if target == "brand_code" else "OE 号"

    existing = [line for line in clean_oe_list(product[field]).split("\n") if line]
    existing_keys = {normalize_code(line) for line in existing}
    if normalize_code(code) in existing_keys:
        return False

    updated = "\n".join(existing + [code])
    conn.execute(
        f"UPDATE products SET {field} = ?, updated_at = ? WHERE id = ?",
        (updated, now_text(), product["id"]),
    )
    log_event(conn, f"追加{label}", "product", product["bld_no"], f"{label}新增: {code}", actor=actor)
    conn.commit()
    return True


def append_product_oe(conn: sqlite3.Connection, bld_no: str, oe_code: str, actor: str = "") -> bool:
    return append_product_code(conn, bld_no, oe_code, target="oe", actor=actor)


def delete_alias(conn: sqlite3.Connection, source_code: str, actor: str = "") -> None:
    key = normalize_code(source_code)
    conn.execute("UPDATE aliases SET active = 0, updated_at = ? WHERE source_code = ?", (now_text(), key))
    log_event(conn, "删除人工映射", "alias", key, "", actor=actor)
    conn.commit()


def list_aliases(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM aliases WHERE active = 1 ORDER BY source_code").fetchall()


def rows_for_catalog(conn: sqlite3.Connection) -> tuple[list[dict], dict[str, str]]:
    products = []
    for row in conn.execute("SELECT * FROM products WHERE active = 1 ORDER BY bld_no"):
        products.append(
            {
                "BLD NO.": row["bld_no"],
                "SERIES": row["series"],
                "ITEM": row["item"],
                "OE NO.1": row["oe_no_1"],
                "OE NO.2": row["oe_no_2"],
                "Models": row["models"],
                "price_cny": row["price_cny"],
                "image_path": row["image_path"],
            }
        )
    aliases = {
        row["source_code"]: row["bld_no"]
        for row in conn.execute("SELECT source_code, bld_no FROM aliases WHERE active = 1")
    }
    return products, aliases


def _material_values_from_data(data: dict, *, source: str = "web", source_row: int = 0) -> dict:
    model = compact_text(data.get("model"))
    if not model:
        raise ValueError("型号不能为空。")
    pieces = _parse_required_float(data.get("pieces"), "下料只数")
    thickness = _parse_required_float(data.get("thickness"), "规格1")
    width = _parse_required_float(data.get("width"), "规格2")
    length = _parse_required_float(data.get("length"), "规格3")
    if pieces <= 0:
        raise ValueError("下料只数必须大于 0。")
    spec_text = compact_text(data.get("spec_text"))
    if not spec_text:
        spec_text = f"{thickness:g}×{width:g}×{length:g}"
    return {
        "model": model,
        "code": compact_text(data.get("code")),
        "category": compact_text(data.get("category")),
        "car": compact_text(data.get("car")),
        "part": compact_text(data.get("part")),
        "spec_text": spec_text,
        "pieces": pieces,
        "thickness": thickness,
        "width": width,
        "length": length,
        "active": 1 if str(data.get("active", "1")) != "0" else 0,
        "source": source,
        "source_row": int(source_row or 0),
    }


def _material_changes(before: sqlite3.Row | None, after: dict) -> list[str]:
    labels = {
        "model": "型号",
        "code": "编码",
        "category": "类别",
        "car": "车型",
        "part": "零件名称",
        "spec_text": "规格尺寸",
        "pieces": "下料只数",
        "thickness": "规格1",
        "width": "规格2",
        "length": "规格3",
        "active": "状态",
    }
    if before is None:
        return ["新增材料明细"]
    changes = []
    for field, label in labels.items():
        old = str(before[field] or "")
        new = str(after[field] or "")
        if old != new:
            changes.append(f"{label}: {old or '(空)'} -> {new or '(空)'}")
    return changes


def upsert_material_item(conn: sqlite3.Connection, data: dict, actor: str = "", source: str = "web") -> int:
    timestamp = now_text()
    item_id = data.get("id")
    before = get_material_item(conn, int(item_id)) if item_id else None
    values = _material_values_from_data(data, source=source, source_row=before["source_row"] if before else 0)
    values.update({"updated_at": timestamp})

    if before:
        values["id"] = int(item_id)
        conn.execute(
            """
            UPDATE material_items
            SET model=:model, code=:code, category=:category, car=:car, part=:part,
                spec_text=:spec_text, pieces=:pieces, thickness=:thickness, width=:width,
                length=:length, active=:active, source=:source, updated_at=:updated_at
            WHERE id=:id
            """,
            values,
        )
        item_key = f"{values['model']}-{values['code'] or values['part'] or values['id']}"
        changes = _material_changes(before, values)
        if changes:
            log_event(conn, "编辑材料明细", "material_item", item_key, "\n".join(changes[:20]), actor=actor)
        saved_id = int(item_id)
    else:
        values.update({"created_at": timestamp})
        cursor = conn.execute(
            """
            INSERT INTO material_items
              (model, code, category, car, part, spec_text, pieces, thickness, width, length,
               active, source, source_row, created_at, updated_at)
            VALUES
              (:model, :code, :category, :car, :part, :spec_text, :pieces, :thickness, :width, :length,
               :active, :source, :source_row, :created_at, :updated_at)
            """,
            values,
        )
        saved_id = int(cursor.lastrowid)
        item_key = f"{values['model']}-{values['code'] or values['part'] or saved_id}"
        log_event(conn, "新增材料明细", "material_item", item_key, "新增材料明细", actor=actor)
    conn.commit()
    return saved_id


def import_materials_from_excel(conn: sqlite3.Connection, material_path: Path, replace: bool = True, actor: str = "") -> int:
    from openpyxl import load_workbook

    wb = load_workbook(material_path, read_only=True, data_only=True)
    if "材料数据" not in wb.sheetnames:
        raise ValueError("材料数据文件里找不到工作表：材料数据")
    ws = wb["材料数据"]
    timestamp = now_text()
    rows: list[dict] = []
    for row_number, values in enumerate(ws.iter_rows(min_row=2, max_col=11, values_only=True), start=2):
        data = {
            "model": values[0],
            "code": values[1],
            "category": values[2],
            "car": values[3],
            "part": values[4],
            "spec_text": values[5],
            "pieces": values[6],
            "thickness": values[8],
            "width": values[9],
            "length": values[10],
            "active": 1,
        }
        if not compact_text(data["model"]):
            continue
        if any(value in (None, "") for value in [data["pieces"], data["thickness"], data["width"], data["length"]]):
            continue
        row = _material_values_from_data(data, source=material_path.name, source_row=row_number)
        row.update({"created_at": timestamp, "updated_at": timestamp})
        rows.append(row)

    if not rows:
        raise ValueError("材料数据里没有可导入的明细。")
    if replace:
        conn.execute("DELETE FROM material_items")
    conn.executemany(
        """
        INSERT INTO material_items
          (model, code, category, car, part, spec_text, pieces, thickness, width, length,
           active, source, source_row, created_at, updated_at)
        VALUES
          (:model, :code, :category, :car, :part, :spec_text, :pieces, :thickness, :width, :length,
           :active, :source, :source_row, :created_at, :updated_at)
        """,
        rows,
    )
    log_event(conn, "导入材料数据", "material_data", material_path.name, f"导入 {len(rows)} 行材料明细", actor=actor)
    conn.commit()
    return len(rows)


def bootstrap_materials_from_excel(db_path: Path, material_path: Path) -> None:
    with connect(db_path) as conn:
        existing = conn.execute("SELECT COUNT(*) FROM material_items").fetchone()[0]
        if existing == 0 and material_path.exists():
            import_materials_from_excel(conn, material_path, replace=True, actor="system")


def list_material_items(
    conn: sqlite3.Connection,
    query: str = "",
    include_inactive: bool = False,
    only_inactive: bool = False,
    limit: int = 3000,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM material_items"
    params: list[object] = []
    clauses = []
    if only_inactive:
        clauses.append("active = 0")
    elif not include_inactive:
        clauses.append("active = 1")
    if query.strip():
        key = f"%{query.strip()}%"
        clauses.append(
            "(model LIKE ? OR code LIKE ? OR category LIKE ? OR car LIKE ? OR part LIKE ? OR spec_text LIKE ?)"
        )
        params.extend([key, key, key, key, key, key])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY model, code, id LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_material_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM material_items WHERE id = ?", (item_id,)).fetchone()


def deactivate_material_item(conn: sqlite3.Connection, item_id: int, actor: str = "") -> None:
    row = get_material_item(conn, item_id)
    conn.execute("UPDATE material_items SET active = 0, updated_at = ? WHERE id = ?", (now_text(), item_id))
    if row:
        key = f"{row['model']}-{row['code'] or row['part'] or row['id']}"
        log_event(conn, "停用材料明细", "material_item", key, "状态: 启用 -> 停用", actor=actor)
    conn.commit()


def material_item_stats(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(active = 1) AS active, SUM(active = 0) AS inactive FROM material_items"
    ).fetchone()
    model_count = conn.execute("SELECT COUNT(DISTINCT model) FROM material_items WHERE active = 1").fetchone()[0]
    return {
        "items": row["total"] or 0,
        "active": row["active"] or 0,
        "inactive": row["inactive"] or 0,
        "models": model_count or 0,
    }


def rows_for_material_sheet(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    rows_by_model: dict[str, list[dict]] = defaultdict(list)
    for row in conn.execute("SELECT * FROM material_items WHERE active = 1 ORDER BY model, code, id"):
        rows_by_model[row["model"]].append(
            {
                "source_row": row["source_row"],
                "model": row["model"],
                "code": row["code"],
                "category": row["category"],
                "car": row["car"],
                "part": row["part"],
                "spec_text": row["spec_text"],
                "pieces": float(row["pieces"]),
                "thickness": float(row["thickness"]),
                "width": float(row["width"]),
                "length": float(row["length"]),
            }
        )
    return rows_by_model


def list_audit_logs(conn: sqlite3.Connection, query: str = "", actor: str = "", limit: int = 300) -> list[sqlite3.Row]:
    sql = "SELECT * FROM audit_logs"
    params: list[object] = []
    clauses = []
    if query.strip():
        key = f"%{query.strip()}%"
        clauses.append("(target_key LIKE ? OR detail LIKE ? OR action LIKE ? OR actor LIKE ?)")
        params.extend([key, key, key, key])
    if actor.strip():
        clauses.append("actor = ?")
        params.append(actor.strip())
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def list_log_actors(conn: sqlite3.Connection) -> list[str]:
    return [
        row["actor"]
        for row in conn.execute(
            "SELECT DISTINCT actor FROM audit_logs WHERE actor IS NOT NULL AND actor != '' ORDER BY actor"
        )
    ]


def ensure_default_admin(conn: sqlite3.Connection, username: str = "007", password: str = "4r3e2w1q") -> None:
    existing = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        if str(existing["password_hash"] or "").startswith("scrypt:"):
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(password), now_text(), existing["id"]),
            )
            log_event(conn, "迁移管理员密码", "user", username, "切换为兼容的密码哈希算法", actor="system")
            conn.commit()
        return
    timestamp = now_text()
    conn.execute(
        """
        INSERT INTO users (username, display_name, password_hash, role, active, created_at, updated_at)
        VALUES (?, ?, ?, 'admin', 1, ?, ?)
        """,
        (username, "管理员", hash_password(password), timestamp, timestamp),
    )
    log_event(conn, "初始化管理员", "user", username, "创建默认管理员账号", actor="system")
    conn.commit()


def get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM users ORDER BY active DESC, username").fetchall()


def save_user(conn: sqlite3.Connection, data: dict, actor: str = "") -> None:
    timestamp = now_text()
    user_id = data.get("id")
    username = compact_text(data.get("username"))
    if not username:
        raise ValueError("登录名不能为空。")
    role = data.get("role") or "viewer"
    if role not in {"admin", "editor", "user", "viewer"}:
        raise ValueError("角色无效。")
    active = 1 if str(data.get("active", "1")) != "0" else 0
    display_name = compact_text(data.get("display_name"))
    password = str(data.get("password") or "")

    if user_id:
        before = get_user(conn, int(user_id))
        if not before:
            raise ValueError("用户不存在。")
        params = {
            "id": int(user_id),
            "username": username,
            "display_name": display_name,
            "role": role,
            "active": active,
            "updated_at": timestamp,
        }
        password_sql = ""
        if password:
            params["password_hash"] = hash_password(password)
            password_sql = ", password_hash=:password_hash"
        conn.execute(
            f"""
            UPDATE users
            SET username=:username, display_name=:display_name, role=:role, active=:active,
                updated_at=:updated_at {password_sql}
            WHERE id=:id
            """,
            params,
        )
        changes = []
        for field, label in {"username": "登录名", "display_name": "显示名", "role": "角色", "active": "状态"}.items():
            if str(before[field] or "") != str(params[field] or ""):
                changes.append(f"{label}: {before[field]} -> {params[field]}")
        if password:
            changes.append("密码已重置")
        if changes:
            log_event(conn, "编辑账号", "user", username, "\n".join(changes), actor=actor)
    else:
        if not password:
            raise ValueError("新增用户必须设置密码。")
        conn.execute(
            """
            INSERT INTO users (username, display_name, password_hash, role, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (username, display_name, hash_password(password), role, active, timestamp, timestamp),
        )
        log_event(conn, "新增账号", "user", username, f"角色: {role}", actor=actor)
    conn.commit()
