from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from typing import Any, cast

from app.platform.audit_store import log_event
from app.platform.clock import now_text
from app.platform.sync_identity import MATERIAL_IDENTITY_FIELDS, material_key, stable_sync_id

from .weights import validated_net_weight
from .item_filter_sql import EMPTY_FILTER_VALUE, MATERIAL_COLUMN_EXPRESSIONS, UNIT_WEIGHT_SQL, material_filter_clauses
from .specification import _material_values_from_data


def _material_changes(before: sqlite3.Row | None, after: Mapping[str, Any]) -> list[str]:
    labels = {
        "model": "母件编码",
        "code": "零件编码",
        "category": "类别",
        "car": "车型",
        "part": "零件名称",
        "material_grade": "材料牌号",
        "spec_text": "规格尺寸",
        "pieces": "下料只数",
        "net_weight": "净重（kg/件）",
        "thickness": "规格1",
        "width": "规格2",
        "length": "规格3",
        "active": "状态",
    }
    if before is None:
        return ["新增材料明细"]
    return [
        f"{label}: {str(before[field] or '') or '(空)'} -> {str(after[field] or '') or '(空)'}"
        for field, label in labels.items()
        if str(before[field] or "") != str(after[field] or "")
    ]


def get_material_item(connection: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM material_items WHERE id = ?", (item_id,)).fetchone()


def upsert_material_item(
    connection: sqlite3.Connection,
    data: Mapping[str, object],
    actor: str = "",
    source: str = "web",
    *,
    commit: bool = True,
) -> int:
    timestamp = now_text()
    item_id = data.get("id")
    original = None
    original_spec = None
    if data.get("copy_from"):
        if item_id:
            raise ValueError("复制新建时不能指定要编辑的记录。")
        original = connection.execute("SELECT * FROM material_items WHERE sync_id = ?", (data["copy_from"],)).fetchone()
        if original is None:
            raise ValueError("原材料明细已变更，请刷新列表后重新复制。")
        if data.get("spec_text") == original["spec_text"]:
            # The label can be rounded or a legacy description. Copy its actual dimensions.
            original_spec = original["spec_text"]
            data = {**data, "spec_text": "", **{k: original[k] for k in ("thickness", "width", "length")}}
    before = get_material_item(connection, int(cast(Any, item_id))) if item_id else None
    if item_id and before is None:
        raise ValueError("这条材料明细已变更，请在列表中重新打开。")
    if before and "original_sync_id" in data and data["original_sync_id"] != (before["sync_id"] or ""):
        raise ValueError("这条材料明细的身份已变更，请重新打开核对。")
    values = _material_values_from_data(
        data,
        source=source,
        source_row=before["source_row"] if before else 0,
    )
    if original_spec is not None:
        values["spec_text"] = original_spec
    if original is not None and values["code"].casefold() == original["code"].casefold():
        raise ValueError("请填写与原件不同的新零件编码。")
    values["net_weight"] = validated_net_weight(values, data.get("net_weight", before["net_weight"] if before else None))
    values["updated_at"] = timestamp
    if before:
        values["id"] = int(cast(Any, item_id))
        connection.execute(
            """
            UPDATE material_items
            SET model=:model, code=:code, category=:category, car=:car, part=:part,
                spec_text=:spec_text, pieces=:pieces, thickness=:thickness, width=:width,
                length=:length, net_weight=:net_weight, material_grade=:material_grade, active=:active, source=:source, updated_at=:updated_at
            WHERE id=:id
            """,
            values,
        )
        saved_id = int(cast(Any, item_id))
        changes = _material_changes(before, values)
        if changes:
            log_event(
                connection,
                "编辑材料明细",
                "material_item",
                f"{values['model']}-{values['code'] or values['part'] or values['id']}",
                "\n".join(changes[:20]),
                actor=actor,
            )
    else:
        values["created_at"] = timestamp
        key = material_key(values)
        existing_rows = connection.execute(
            f"SELECT sync_id, {', '.join(MATERIAL_IDENTITY_FIELDS)} FROM material_items"
        ).fetchall()
        ordinal = 1 + sum(material_key(dict(row)) == key for row in existing_rows)
        if original is not None and ordinal > 1:
            raise ValueError("该母件已有相同编码和下料资料的材料明细。")
        # Renamed materials retain their synchronization identity on other devices.
        used_sync_ids = {row["sync_id"] for row in existing_rows}
        sync_id = stable_sync_id("material", key, ordinal)
        while sync_id in used_sync_ids:
            ordinal += 1
            sync_id = stable_sync_id("material", key, ordinal)
        values["sync_id"] = sync_id
        cursor = connection.execute(
            """
            INSERT INTO material_items
              (model, code, category, car, part, spec_text, pieces, thickness, width, length,
               net_weight, material_grade, active, source, source_row, sync_id, created_at, updated_at)
            VALUES
              (:model, :code, :category, :car, :part, :spec_text, :pieces, :thickness, :width,
               :length, :net_weight, :material_grade, :active, :source, :source_row, :sync_id, :created_at, :updated_at)
            """,
            values,
        )
        if cursor.lastrowid is None:
            raise RuntimeError("材料明细写入后没有返回 ID。")
        saved_id = int(cursor.lastrowid)
        log_event(
            connection,
            "新增材料明细",
            "material_item",
            f"{values['model']}-{values['code'] or values['part'] or saved_id}",
            f"从 {original['model']} / {original['code']} 复制新建" if original is not None else "新增材料明细",
            actor=actor,
        )
    if commit:
        connection.commit()
    return saved_id


def count_material_items(
    connection: sqlite3.Connection,
    query: str = "",
    include_inactive: bool = False,
    only_inactive: bool = False,
    column_filters: Mapping[str, tuple[str, ...]] | None = None,
) -> int:
    sql = "SELECT COUNT(*) FROM material_items"
    clauses, params = material_filter_clauses(query, include_inactive, only_inactive, column_filters)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    row = connection.execute(sql, params).fetchone()
    return int(row[0] or 0) if row is not None else 0


def list_material_items(
    connection: sqlite3.Connection,
    query: str = "",
    include_inactive: bool = False,
    only_inactive: bool = False,
    limit: int = 3000,
    offset: int = 0,
    column_filters: Mapping[str, tuple[str, ...]] | None = None,
) -> list[sqlite3.Row]:
    sql = f"""
        SELECT *, {UNIT_WEIGHT_SQL} AS unit_weight,
               {MATERIAL_COLUMN_EXPRESSIONS["scrap_weight"]} AS scrap_weight,
               {MATERIAL_COLUMN_EXPRESSIONS["scrap_percent"]} AS scrap_percent
        FROM material_items
    """
    clauses, params = material_filter_clauses(query, include_inactive, only_inactive, column_filters)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY model, code, id LIMIT ? OFFSET ?"
    params.extend((limit, max(0, offset)))
    return connection.execute(sql, params).fetchall()


def material_filter_options(
    connection: sqlite3.Connection,
    *,
    query: str,
    include_inactive: bool,
    only_inactive: bool,
    column_filters: Mapping[str, tuple[str, ...]],
) -> dict[str, list[dict[str, object]]]:
    options: dict[str, list[dict[str, object]]] = {}
    for field, expression in MATERIAL_COLUMN_EXPRESSIONS.items():
        remaining = {key: value for key, value in column_filters.items() if key != field}
        clauses, params = material_filter_clauses(query, include_inactive, only_inactive, remaining)
        sql = f"SELECT {expression} AS value, COUNT(*) AS count FROM material_items"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" GROUP BY {expression} ORDER BY count DESC, value COLLATE NOCASE"
        rows = connection.execute(sql, params).fetchall()
        options[field] = [
            {
                "value": str(row["value"]) if row["value"] else EMPTY_FILTER_VALUE,
                "label": ("启用" if row["value"] == "active" else "停用")
                if field == "active"
                else (str(row["value"]) if row["value"] else "（空）"),
                "count": int(row["count"]),
            }
            for row in rows
        ]
    return options


def deactivate_material_item(
    connection: sqlite3.Connection,
    item_id: int,
    actor: str = "",
    *,
    commit: bool = True,
) -> None:
    row = get_material_item(connection, item_id)
    connection.execute(
        "UPDATE material_items SET active = 0, updated_at = ? WHERE id = ?",
        (now_text(), item_id),
    )
    if row:
        log_event(
            connection,
            "停用材料明细",
            "material_item",
            f"{row['model']}-{row['code'] or row['part'] or row['id']}",
            "状态: 启用 -> 停用",
            actor=actor,
        )
    if commit:
        connection.commit()


def material_item_stats(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        "SELECT COUNT(*) AS total, SUM(active = 1) AS active, SUM(active = 0) AS inactive FROM material_items"
    ).fetchone()
    models_row = connection.execute("SELECT COUNT(DISTINCT model) FROM material_items WHERE active = 1").fetchone()
    models = int(models_row[0] or 0) if models_row is not None else 0
    return {
        "items": int(row["total"] or 0) if row is not None else 0,
        "active": int(row["active"] or 0) if row is not None else 0,
        "inactive": int(row["inactive"] or 0) if row is not None else 0,
        "models": models,
    }


def rows_for_material_sheet(connection: sqlite3.Connection) -> dict[str, list[dict[str, object]]]:
    rows_by_model: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in connection.execute("SELECT * FROM material_items WHERE active = 1 ORDER BY model, code, id"):
        rows_by_model[str(row["model"])].append(
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
