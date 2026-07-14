from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from app.database import connect
from app.matcher import PSA_352X_BRANDS, ProductCatalog, compact_text, normalize_code, psa_352x_key, split_codes
from app.platform.audit_store import log_event
from app.platform.clock import now_text

from .brand_normalization import canonicalize_brands

_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAPPED_SOURCES: set[tuple[Path, Path, int | None]] = set()


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


def _field_changes(before: sqlite3.Row | None, after: dict[str, object]) -> list[str]:
    labels = {
        "series": "品牌",
        "item": "产品名称",
        "oe_no_1": "OE 号",
        "oe_no_2": "品牌号码",
        "models": "车型",
        "price_cny": "含税单价",
        "product_status": "产品状态",
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


def upsert_product(
    connection: sqlite3.Connection,
    data: dict,
    source: str = "manual",
    audit: bool = True,
    actor: str = "",
    *,
    commit: bool = True,
    preserve_blank_price: bool = True,
) -> None:
    timestamp = now_text()
    bld_no = compact_text(data.get("bld_no") or data.get("BLD NO."))
    if not bld_no:
        raise ValueError("BLD NO. 不能为空。")
    values = {
        "bld_no": bld_no,
        "series": canonicalize_brands(data.get("series") or data.get("SERIES")),
        "item": clean_multiline(data.get("item") or data.get("ITEM")),
        "oe_no_1": clean_oe_list(data.get("oe_no_1") or data.get("OE NO.1")),
        "oe_no_2": clean_oe_list(data.get("oe_no_2") or data.get("OE NO.2")),
        "models": clean_multiline(data.get("models") or data.get("Models")),
        "price_cny": _parse_price(data.get("price_cny")),
        "product_status": clean_multiline(data.get("product_status") or data.get("产品状态")),
        "image_path": compact_text(data.get("image_path")),
        "active": 1 if str(data.get("active", "1")) != "0" else 0,
        "source": source,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    before = connection.execute("SELECT * FROM products WHERE bld_no = ?", (bld_no,)).fetchone()
    price_assignment = (
        "price_cny=COALESCE(excluded.price_cny, products.price_cny)"
        if preserve_blank_price
        else "price_cny=excluded.price_cny"
    )
    connection.execute(
        f"""
        INSERT INTO products
          (bld_no, series, item, oe_no_1, oe_no_2, models, price_cny, product_status,
           image_path, active, source, created_at, updated_at)
        VALUES
          (:bld_no, :series, :item, :oe_no_1, :oe_no_2, :models, :price_cny,
           :product_status, :image_path, :active, :source, :created_at, :updated_at)
        ON CONFLICT(bld_no) DO UPDATE SET
          series=excluded.series, item=excluded.item, oe_no_1=excluded.oe_no_1,
          oe_no_2=excluded.oe_no_2, models=excluded.models, {price_assignment},
          product_status=excluded.product_status,
          image_path=CASE WHEN excluded.image_path != '' THEN excluded.image_path ELSE products.image_path END,
          active=excluded.active, source=excluded.source, updated_at=excluded.updated_at
        """,
        values,
    )
    after = connection.execute("SELECT * FROM products WHERE bld_no = ?", (bld_no,)).fetchone()
    if audit and after:
        changes = _field_changes(before, dict(after))
        if changes:
            log_event(
                connection,
                "新增产品" if before is None else "编辑产品",
                "product",
                bld_no,
                "\n".join(changes[:20]),
                actor=actor,
            )
    if commit:
        connection.commit()


def import_catalog(
    connection: sqlite3.Connection,
    catalog_path: Path,
    replace: bool = False,
    actor: str = "",
    *,
    commit: bool = True,
) -> int:
    catalog = ProductCatalog.from_excel(catalog_path)
    if replace:
        connection.execute("DELETE FROM products")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in catalog.rows:
        bld_no = compact_text(row.get("BLD NO."))
        if bld_no:
            grouped[bld_no].append(row)
    for bld_no, rows in grouped.items():
        upsert_product(
            connection,
            {
                "BLD NO.": bld_no,
                "SERIES": merge_unique_lines([row.get("SERIES") for row in rows]),
                "ITEM": merge_unique_lines([row.get("ITEM") for row in rows]),
                "OE NO.1": merge_unique_lines([row.get("OE NO.1") for row in rows], oe=True),
                "OE NO.2": merge_unique_lines([row.get("OE NO.2") for row in rows], oe=True),
                "Models": merge_unique_lines([row.get("Models") for row in rows]),
                "price_cny": None,
                "product_status": "",
                "image_path": "",
            },
            source=catalog_path.name,
            audit=False,
            commit=False,
        )
    log_event(
        connection,
        "导入目录",
        "catalog",
        catalog_path.name,
        f"汇总导入 {len(grouped)} 个唯一 BLD 号",
        actor=actor,
    )
    if commit:
        connection.commit()
    return len(grouped)


def bootstrap_from_excel(database_path: Path, catalog_path: Path) -> None:
    source_mtime = catalog_path.stat().st_mtime_ns if catalog_path.exists() else None
    key = (database_path.resolve(), catalog_path.resolve(), source_mtime)
    with _BOOTSTRAP_LOCK:
        if key in _BOOTSTRAPPED_SOURCES:
            return
        with connect(database_path) as connection:
            existing = int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0])
            if existing == 0 and catalog_path.exists():
                import_catalog(connection, catalog_path, replace=True)
        _BOOTSTRAPPED_SOURCES.add(key)


def _normalized_code_sql(column: str) -> str:
    expression = f"UPPER({column})"
    for item in ("-", " ", ".", "/", "_"):
        expression = f"REPLACE({expression}, '{item}', '')"
    for char_code, replacement in ((9, ""), (10, "|"), (13, "|")):
        expression = f"REPLACE({expression}, CHAR({char_code}), '{replacement}')"
    return expression


def _psa_product_clause() -> tuple[str, list[str]]:
    checks: list[str] = []
    params: list[str] = []
    for brand in PSA_352X_BRANDS:
        checks.extend(("UPPER(series) LIKE ?", "UPPER(models) LIKE ?"))
        params.extend((f"%{brand}%", f"%{brand}%"))
    return "(" + " OR ".join(checks) + ")", params


def _product_status_key_sql(column: str) -> str:
    return f"PRODUCT_STATUS_KEY(COALESCE({column}, ''))"


def _append_column_filters(
    clauses: list[str],
    params: list[object],
    *,
    brands: Sequence[str],
    items: Sequence[str],
    product_statuses: Sequence[str],
    brand_blank: bool,
    item_blank: bool,
    product_status_blank: bool,
) -> None:
    if brands or brand_blank:
        brand_checks: list[str] = []
        normalized_series = "REPLACE(COALESCE(series, ''), CHAR(13), CHAR(10))"
        for brand in brands:
            brand_checks.append(
                f"INSTR(CHAR(10) || UPPER({normalized_series}) || CHAR(10), "
                "CHAR(10) || UPPER(?) || CHAR(10)) > 0"
            )
            params.append(brand)
        if brand_blank:
            brand_checks.append("TRIM(COALESCE(series, '')) = ''")
        clauses.append("(" + " OR ".join(brand_checks) + ")")

    if items or item_blank:
        item_checks: list[str] = []
        for item in items:
            item_checks.append("item COLLATE NOCASE = ?")
            params.append(item)
        if item_blank:
            item_checks.append("TRIM(COALESCE(item, '')) = ''")
        clauses.append("(" + " OR ".join(item_checks) + ")")

    if product_statuses or product_status_blank:
        status_checks: list[str] = []
        normalized_status = _product_status_key_sql("product_status")
        for product_status in product_statuses:
            status_checks.append(f"{normalized_status} COLLATE NOCASE = ?")
            params.append(product_status)
        if product_status_blank:
            status_checks.append(f"{normalized_status} = ''")
        clauses.append("(" + " OR ".join(status_checks) + ")")


def _product_filter_clauses(
    query: str = "",
    include_inactive: bool = False,
    only_inactive: bool = False,
    bld_query: str = "",
    oe_query: str = "",
    series_query: str = "",
    model_query: str = "",
    brands: Sequence[str] = (),
    items: Sequence[str] = (),
    product_statuses: Sequence[str] = (),
    brand_blank: bool = False,
    item_blank: bool = False,
    product_status_blank: bool = False,
) -> tuple[list[str], list[object]]:
    params: list[object] = []
    clauses: list[str] = []
    if only_inactive:
        clauses.append("active = 0")
    elif not include_inactive:
        clauses.append("active = 1")
    if query.strip() and not bld_query.strip() and not oe_query.strip():
        bld_query = query
    if bld_query.strip():
        key = f"%{bld_query.strip().upper()}%"
        clauses.append("(UPPER(bld_no) LIKE ? OR UPPER(series) LIKE ? OR UPPER(models) LIKE ?)")
        params.extend((key, key, key))
    if oe_query.strip():
        psa_probe = psa_352x_key(oe_query)
        key = f"%{psa_probe[0] if psa_probe else normalize_code(oe_query)}%"
        oe_clause = f"({_normalized_code_sql('oe_no_1')} LIKE ? OR {_normalized_code_sql('oe_no_2')} LIKE ?)"
        if psa_probe and psa_probe[1]:
            psa_clause, psa_params = _psa_product_clause()
            clauses.append(f"({oe_clause} AND {psa_clause})")
            params.extend((key, key, *psa_params))
        else:
            clauses.append(oe_clause)
            params.extend((key, key))
    if series_query.strip():
        clauses.append("UPPER(series) LIKE ?")
        params.append(f"%{series_query.strip().upper()}%")
    if model_query.strip():
        clauses.append("UPPER(models) LIKE ?")
        params.append(f"%{model_query.strip().upper()}%")
    _append_column_filters(
        clauses,
        params,
        brands=brands,
        items=items,
        product_statuses=product_statuses,
        brand_blank=brand_blank,
        item_blank=item_blank,
        product_status_blank=product_status_blank,
    )
    return clauses, params


def list_products(
    connection: sqlite3.Connection,
    query: str = "",
    include_inactive: bool = False,
    only_inactive: bool = False,
    limit: int | None = 3000,
    bld_query: str = "",
    oe_query: str = "",
    series_query: str = "",
    model_query: str = "",
    offset: int = 0,
    brands: Sequence[str] = (),
    items: Sequence[str] = (),
    product_statuses: Sequence[str] = (),
    brand_blank: bool = False,
    item_blank: bool = False,
    product_status_blank: bool = False,
    sort_by: str = "bld",
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM products"
    clauses, params = _product_filter_clauses(
        query,
        include_inactive,
        only_inactive,
        bld_query,
        oe_query,
        series_query,
        model_query,
        brands,
        items,
        product_statuses,
        brand_blank,
        item_blank,
        product_status_blank,
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    if sort_by == "series":
        sql += " ORDER BY series COLLATE NOCASE, bld_no COLLATE BLD_NATURAL"
    else:
        sql += " ORDER BY bld_no COLLATE BLD_NATURAL"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend((max(0, limit), max(0, offset)))
    return connection.execute(sql, params).fetchall()


def count_products(
    connection: sqlite3.Connection,
    query: str = "",
    include_inactive: bool = False,
    only_inactive: bool = False,
    bld_query: str = "",
    oe_query: str = "",
    series_query: str = "",
    model_query: str = "",
    brands: Sequence[str] = (),
    items: Sequence[str] = (),
    product_statuses: Sequence[str] = (),
    brand_blank: bool = False,
    item_blank: bool = False,
    product_status_blank: bool = False,
) -> int:
    sql = "SELECT COUNT(*) FROM products"
    clauses, params = _product_filter_clauses(
        query,
        include_inactive,
        only_inactive,
        bld_query,
        oe_query,
        series_query,
        model_query,
        brands,
        items,
        product_statuses,
        brand_blank,
        item_blank,
        product_status_blank,
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return int(connection.execute(sql, params).fetchone()[0] or 0)


def product_stats(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        "SELECT COUNT(*) AS total, SUM(active = 1) AS active, SUM(active = 0) AS inactive FROM products"
    ).fetchone()
    alias_count = int(connection.execute("SELECT COUNT(*) FROM aliases WHERE active = 1").fetchone()[0] or 0)
    return {
        "products": int(row["total"] or 0),
        "active": int(row["active"] or 0),
        "inactive": int(row["inactive"] or 0),
        "aliases": alias_count,
    }


def rows_for_catalog(connection: sqlite3.Connection) -> tuple[list[dict], dict[str, str]]:
    products = []
    for row in connection.execute("SELECT * FROM products WHERE active = 1 ORDER BY bld_no COLLATE BLD_NATURAL"):
        keys = set(row.keys())
        products.append(
            {
                "BLD NO.": row["bld_no"],
                "SERIES": row["series"],
                "ITEM": row["item"],
                "OE NO.1": row["oe_no_1"],
                "OE NO.2": row["oe_no_2"],
                "Models": row["models"],
                "price_cny": row["price_cny"],
                "product_status": row["product_status"] if "product_status" in keys else "",
                **{
                    field: row[field] if field in keys else ""
                    for field in ("image_path", "image_path_2", "image_path_3", "image_path_4", "image_path_5")
                },
            }
        )
    aliases = {
        str(row["source_code"]): str(row["bld_no"])
        for row in connection.execute("SELECT source_code, bld_no FROM aliases WHERE active = 1")
    }
    return products, aliases
