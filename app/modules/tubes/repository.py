from __future__ import annotations

import sqlite3
import re
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import cast

from app.database import connect
from app.platform.audit_store import log_event
from app.platform.clock import now_text

from .domain import TUBE_TYPES, TubeImportRow, clean_text, normalize_spec_search, number_or_none


class TubeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def list(self, *, filters: Mapping[str, object], limit: int, offset: int) -> list[dict[str, object]]:
        clauses, parameters = self._where(filters, item_alias="item")
        where = " AND ".join(clauses)
        parameters.extend([limit, offset])
        return [
            dict(row)
            for row in self.connection.execute(
                f"""
                SELECT id, code, tube_type, spec_text, weight_kg, tolerance_mm,
                       consumption_mm, outer_diameter_mm, inner_diameter_mm, blank_length_text,
                       inner_diameter_tolerance, purchase_base, borrowed_from, note, active,
                       COALESCE((
                         SELECT GROUP_CONCAT(alias.code, '、')
                         FROM tube_items AS alias
                         WHERE alias.active = 1 AND alias.borrowed_from = item.code
                       ), '') AS borrowed_codes
                FROM tube_items AS item
                WHERE {where}
                ORDER BY item.code COLLATE BLD_NATURAL
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        ]

    def count(self, *, filters: Mapping[str, object]) -> int:
        clauses, parameters = self._where(filters, item_alias="item")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM tube_items AS item WHERE {' AND '.join(clauses)}", parameters).fetchone()[0])

    @staticmethod
    def _where(filters: Mapping[str, object], *, item_alias: str = "") -> tuple[list[str], list[object]]:
        prefix = f"{item_alias}." if item_alias else ""
        clauses = [
            f"{prefix}active = 1",
            f"({prefix}borrowed_from = '' OR NOT EXISTS (SELECT 1 FROM tube_items AS source WHERE source.active = 1 AND source.code = {prefix}borrowed_from))",
        ]
        parameters: list[object] = []
        query = str(filters.get("query", "")).strip()
        if query.strip():
            needle = f"%{query}%"
            spec_needle = f"%{normalize_spec_search(query)}%"
            clauses.append(
                f"({prefix}code LIKE ? OR REPLACE(REPLACE(REPLACE(REPLACE({prefix}spec_text, ' ', ''), '*', '×'), 'x', '×'), 'X', '×') LIKE ? OR {prefix}borrowed_from LIKE ? OR {prefix}note LIKE ? OR EXISTS (SELECT 1 FROM tube_items AS alias WHERE alias.active = 1 AND alias.borrowed_from = {prefix}code AND alias.code LIKE ?))"
            )
            parameters.extend([needle, spec_needle, needle, needle, needle])
        for column, key in (("tube_type", "tube_types"), ("tolerance_mm", "tolerances"), ("consumption_mm", "consumptions")):
            values = cast(tuple[object, ...], filters.get(key, ()))
            if values:
                clauses.append(f"{prefix}{column} IN ({', '.join('?' for _ in values)})")
                parameters.extend(values)
        for column, key, operator in (("weight_kg", "weight_eq", "="), ("weight_kg", "weight_min", ">="), ("weight_kg", "weight_max", "<="), ("outer_diameter_mm", "outer_diameter", "="), ("inner_diameter_mm", "inner_diameter", "=")):
            value = filters.get(key)
            if value is not None:
                clauses.append(f"{prefix}{column} {operator} ?")
                parameters.append(value)
        return clauses, parameters

    def type_counts(self) -> dict[str, int]:
        counts = {tube_type: 0 for tube_type in TUBE_TYPES}
        for row in self.connection.execute("SELECT item.tube_type, COUNT(*) AS count FROM tube_items AS item WHERE item.active = 1 AND (item.borrowed_from = '' OR NOT EXISTS (SELECT 1 FROM tube_items AS source WHERE source.active = 1 AND source.code = item.borrowed_from)) GROUP BY item.tube_type"):
            if row["tube_type"] in counts:
                counts[row["tube_type"]] = int(row["count"])
        return counts

    def number_counts(self, column: str) -> list[dict[str, object]]:
        if column not in {"tolerance_mm", "consumption_mm"}:
            raise ValueError("不支持的管件数值筛选列。")
        return [
            {"value": float(row["value"]), "label": f"{float(row['value']):g}", "count": int(row["count"])}
            for row in self.connection.execute(
                f"SELECT item.{column} AS value, COUNT(*) AS count FROM tube_items AS item WHERE item.active = 1 AND (item.borrowed_from = '' OR NOT EXISTS (SELECT 1 FROM tube_items AS source WHERE source.active = 1 AND source.code = item.borrowed_from)) AND item.{column} IS NOT NULL GROUP BY item.{column} ORDER BY item.{column}"
            )
        ]

    def get(self, item_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM tube_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        if not item["borrowed_from"]:
            item["borrowed_codes"] = "\n".join(
                record["code"]
                for record in self.connection.execute(
                    "SELECT code FROM tube_items WHERE borrowed_from = ? ORDER BY code COLLATE BLD_NATURAL",
                    (item["code"],),
                )
            )
        return item

    def save(self, data: Mapping[str, object], *, actor: str) -> int:
        code = clean_text(data.get("code"))
        tube_type = clean_text(data.get("tube_type"))
        spec_text = clean_text(data.get("spec_text"))
        if not code or not tube_type or not spec_text:
            raise ValueError("编号、类型和规格不能为空。")
        if tube_type not in TUBE_TYPES:
            raise ValueError("管件类型无效。")
        item_id = clean_text(data.get("id"))
        existing = (
            self.connection.execute("SELECT code, borrowed_from FROM tube_items WHERE id = ?", (int(item_id),)).fetchone()
            if item_id
            else None
        )
        borrowed_from = self._borrow_root(clean_text(data.get("borrowed_from")))
        if borrowed_from and borrowed_from.upper() == code.upper():
            raise ValueError("管件不能借用自身编号。")
        values = (
            code,
            tube_type,
            spec_text,
            number_or_none(data.get("weight_kg")),
            number_or_none(data.get("tolerance_mm")),
            number_or_none(data.get("consumption_mm")),
            number_or_none(data.get("outer_diameter_mm")),
            number_or_none(data.get("inner_diameter_mm")),
            clean_text(data.get("blank_length_text")),
            clean_text(data.get("inner_diameter_tolerance")),
            max(1, int(number_or_none(data.get("purchase_base")) or 1)),
            borrowed_from,
            clean_text(data.get("note")),
            1 if clean_text(data.get("active", "1")) not in {"0", "false"} else 0,
            now_text(),
        )
        if item_id:
            cursor = self.connection.execute(
                """
                UPDATE tube_items
                SET code = ?, tube_type = ?, spec_text = ?, weight_kg = ?, tolerance_mm = ?,
                    consumption_mm = ?, outer_diameter_mm = ?, inner_diameter_mm = ?, blank_length_text = ?, inner_diameter_tolerance = ?, purchase_base = ?, borrowed_from = ?, note = ?, active = ?, updated_at = ?
                WHERE id = ?
                """,
                (*values, int(item_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("管件不存在。")
            result_id = int(item_id)
            action = "编辑管件"
        else:
            now = now_text()
            cursor = self.connection.execute(
                """
                INSERT INTO tube_items (
                  code, tube_type, spec_text, weight_kg, tolerance_mm, consumption_mm,
                  outer_diameter_mm, inner_diameter_mm, blank_length_text, inner_diameter_tolerance, purchase_base,
                  borrowed_from, note, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*values[:-1], now, values[-1]),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("管件写入后没有返回 ID。")
            result_id = int(cursor.lastrowid)
            action = "新增管件"
        is_source_item = not borrowed_from and not clean_text(existing["borrowed_from"] if existing is not None else "")
        if existing is not None and not existing["borrowed_from"] and existing["code"] != code:
            self.connection.execute("UPDATE tube_items SET borrowed_from = ?, updated_at = ? WHERE borrowed_from = ?", (code, now_text(), existing["code"]))
        if borrowed_from:
            self._move_descendants_to_root(parent_code=code, root_code=borrowed_from)
        if is_source_item and "borrowed_codes" in data:
            self._sync_borrowed_codes(source_code=code, raw_codes=data.get("borrowed_codes"))
        log_event(self.connection, action, "tube_item", code, f"类型 {tube_type}，规格 {spec_text}", actor=actor)
        return result_id

    def _borrow_root(self, raw_code: str) -> str:
        if not raw_code:
            return ""
        current_code = raw_code.upper()
        visited_codes: set[str] = set()
        while True:
            if current_code in visited_codes:
                raise ValueError("借用关系不能形成循环，请选择原始管件编号。")
            visited_codes.add(current_code)
            row = self.connection.execute("SELECT code, borrowed_from FROM tube_items WHERE code = ?", (current_code,)).fetchone()
            if row is None:
                raise ValueError(f"借用来源编号不存在：{current_code}。")
            borrowed_from = clean_text(row["borrowed_from"])
            if not borrowed_from:
                return clean_text(row["code"])
            current_code = borrowed_from.upper()

    def _descendant_codes(self, parent_code: str) -> set[str]:
        descendants: set[str] = set()
        pending = [parent_code]
        while pending:
            current_code = pending.pop()
            children = [
                clean_text(row["code"])
                for row in self.connection.execute("SELECT code FROM tube_items WHERE borrowed_from = ?", (current_code,))
            ]
            for child_code in children:
                if child_code and child_code not in descendants:
                    descendants.add(child_code)
                    pending.append(child_code)
        return descendants

    def _move_descendants_to_root(self, *, parent_code: str, root_code: str) -> None:
        descendants = self._descendant_codes(parent_code)
        if not descendants:
            return
        self.connection.execute(
            f"UPDATE tube_items SET borrowed_from = ?, updated_at = ? WHERE code IN ({', '.join('?' for _ in descendants)})",
            (root_code, now_text(), *sorted(descendants)),
        )

    def _sync_borrowed_codes(self, *, source_code: str, raw_codes: object) -> None:
        desired_codes = {
            code.upper()
            for code in re.split(r"[\s,，、;；]+", clean_text(raw_codes))
            if code and code.upper() != source_code.upper()
        }
        current_codes = {
            row["code"]
            for row in self.connection.execute("SELECT code FROM tube_items WHERE borrowed_from = ?", (source_code,))
        }
        removed_codes = current_codes - desired_codes
        for removed_code in removed_codes:
            detached_codes = {removed_code, *self._descendant_codes(removed_code)}
            self.connection.execute(
                f"UPDATE tube_items SET borrowed_from = '', updated_at = ? WHERE code IN ({', '.join('?' for _ in detached_codes)})",
                (now_text(), *sorted(detached_codes)),
            )
        for borrowed_code in sorted(desired_codes):
            exists = self.connection.execute("SELECT 1 FROM tube_items WHERE code = ?", (borrowed_code,)).fetchone()
            if exists is not None:
                self.connection.execute("UPDATE tube_items SET borrowed_from = ?, updated_at = ? WHERE code = ?", (source_code, now_text(), borrowed_code))
                self._move_descendants_to_root(parent_code=borrowed_code, root_code=source_code)
                continue
            self.connection.execute(
                """
                INSERT INTO tube_items (
                  code, tube_type, spec_text, weight_kg, tolerance_mm, consumption_mm,
                  outer_diameter_mm, inner_diameter_mm, blank_length_text, inner_diameter_tolerance,
                  purchase_base, borrowed_from, note, active, created_at, updated_at
                )
                SELECT ?, tube_type, spec_text, weight_kg, tolerance_mm, consumption_mm,
                       outer_diameter_mm, inner_diameter_mm, blank_length_text, inner_diameter_tolerance,
                       purchase_base, ?, note, active, ?, ?
                FROM tube_items WHERE code = ?
                """,
                (borrowed_code, source_code, now_text(), now_text(), source_code),
            )

    def import_rows(self, rows: list[TubeImportRow], *, actor: str) -> int:
        now = now_text()
        for row in rows:
            self.connection.execute(
                """
                INSERT INTO tube_items (
                  code, tube_type, spec_text, weight_kg, tolerance_mm, consumption_mm,
                  outer_diameter_mm, inner_diameter_mm, blank_length_text, inner_diameter_tolerance, purchase_base,
                  borrowed_from, note, source_sheet, source_row, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                  tube_type = excluded.tube_type,
                  spec_text = excluded.spec_text,
                  weight_kg = excluded.weight_kg,
                  tolerance_mm = excluded.tolerance_mm,
                  consumption_mm = excluded.consumption_mm,
                  outer_diameter_mm = excluded.outer_diameter_mm,
                  inner_diameter_mm = excluded.inner_diameter_mm,
                  blank_length_text = excluded.blank_length_text,
                  inner_diameter_tolerance = excluded.inner_diameter_tolerance,
                  purchase_base = excluded.purchase_base,
                  borrowed_from = excluded.borrowed_from,
                  note = excluded.note,
                  source_sheet = excluded.source_sheet,
                  source_row = excluded.source_row,
                  active = 1,
                  updated_at = excluded.updated_at
                """,
                (
                    row.code,
                    row.tube_type,
                    row.spec_text,
                    row.weight_kg,
                    row.tolerance_mm,
                    row.consumption_mm,
                    row.outer_diameter_mm,
                    row.inner_diameter_mm,
                    row.blank_length_text,
                    row.inner_diameter_tolerance,
                    row.purchase_base,
                    row.borrowed_from,
                    row.note,
                    row.source_sheet,
                    row.source_row,
                    now,
                    now,
                ),
            )
        log_event(self.connection, "导入管件明细", "tube_import", "2026", f"导入或更新 {len(rows)} 条管件明细", actor=actor)
        return len(rows)


class TubeUnitOfWork:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.connection: sqlite3.Connection | None = None
        self.repository: TubeRepository
        self._committed = False

    def __enter__(self) -> TubeUnitOfWork:
        self.connection = connect(self.database_path)
        self.repository = TubeRepository(self.connection)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Tube unit of work is not active.")
        self.connection.commit()
        self._committed = True

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        if self.connection is None:
            return
        if exc_type is not None or not self._committed:
            self.connection.rollback()
        self.connection.close()
        self.connection = None
