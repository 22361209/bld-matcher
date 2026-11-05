from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .domain import CostingConflict
from .schema import COSTING_TABLES, install_schema


SCHEMA_VERSION = 1
LIST_ORDER = {"updated": "updated_at DESC, code ASC", "code": "code ASC", "code_desc": "code DESC"}
SUMMARY_COLUMNS = ("id", "code", "kind", "name", "brand", "models", "status")
SUMMARY_FIELDS = ("calculated_cost", "approved_cost", "scrap_credit", "net_cost", "suggested_tax_price_override",
                  "scrap_mode", "scrap_complete", "scrap_credit_override", "theoretical_scrap")


def initialize_costing_database(path: Path) -> None:
    """Explicitly initialize an isolated trial store; never migrate an unrelated database."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        names = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith("sqlite_")
        }
        allowed = {"costing_schema", *COSTING_TABLES}
        if names - allowed:
            raise ValueError("成本试用数据库必须独立，不能使用其他业务数据库。")
        if names and "costing_schema" not in names:
            raise ValueError("成本数据库缺少版本标记，请检查初始化状态。")
        if "costing_schema" in names:
            versions = connection.execute("SELECT version FROM costing_schema").fetchall()
            if versions != [(SCHEMA_VERSION,)]:
                raise ValueError("成本数据库版本不受支持。")
        connection.execute("CREATE TABLE IF NOT EXISTS costing_schema (version INTEGER PRIMARY KEY CHECK(version = 1))")
        install_schema(connection)
        if not names:
            connection.execute("INSERT INTO costing_schema(version) VALUES (?)", (SCHEMA_VERSION,))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _filters(q: str, kind: str, status: str) -> tuple[str, list[str]]:
    clauses: list[str] = []
    parameters: list[str] = []
    if q:
        # INSTR treats '%' and '_' as literal search text instead of SQL wildcards.
        clauses.append("(instr(upper(code), ?) OR instr(upper(name), ?) OR instr(upper(brand), ?) OR instr(upper(models), ?))")
        parameters.extend([q.upper()] * 4)
    if kind:
        clauses.append("kind = ?")
        parameters.append(kind)
    if status:
        clauses.append("status = ?")
        parameters.append(status)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), parameters


class SQLiteCostingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, record_id: str) -> dict | None:
        row = self.connection.execute("SELECT payload_json FROM costing_records WHERE id=?", (record_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def find_code(self, code: str) -> dict | None:
        row = self.connection.execute("SELECT payload_json FROM costing_records WHERE code=?", (code,)).fetchone()
        return json.loads(row[0]) if row else None

    def count(self, *, q: str, kind: str, status: str) -> int:
        clause, parameters = _filters(q, kind, status)
        return self.connection.execute("SELECT COUNT(*) FROM costing_records" + clause, parameters).fetchone()[0]

    def list(self, *, q: str, kind: str, status: str, sort: str, limit: int, offset: int) -> list[dict]:
        clause, parameters = _filters(q, kind, status)
        order = LIST_ORDER[sort]
        rows = self.connection.execute(
            "SELECT payload_json FROM costing_records" + clause + f" ORDER BY {order} LIMIT ? OFFSET ?",
            [*parameters, limit, offset],
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def summaries(self, *, q: str, kind: str, status: str, sort: str) -> list[dict]:
        """Read list values only; large detail and import snapshots stay out of filter scans."""
        clause, parameters = _filters(q, kind, status)
        expressions = [*SUMMARY_COLUMNS, *(f"json_extract(payload_json, '$.{field}')" for field in SUMMARY_FIELDS)]
        rows = self.connection.execute(
            "SELECT " + ", ".join(expressions) + " FROM costing_records" + clause + " ORDER BY " + LIST_ORDER[sort],
            parameters,
        ).fetchall()
        fields = (*SUMMARY_COLUMNS, *SUMMARY_FIELDS)
        return [dict(zip(fields, row, strict=True)) for row in rows]

    def pairing_index(self) -> list[dict]:
        rows = self.connection.execute(
            """SELECT id, code, version, json_extract(payload_json, '$.pair.peer_id')
               FROM costing_records ORDER BY code"""
        )
        return [{"id": row[0], "code": row[1], "version": row[2], "peer_id": row[3]} for row in rows]

    def weight_price_lines(self) -> list[dict]:
        """Project candidate rows without loading the workbook evidence or revision history."""
        keys = ("id", "name", "category", "product_code", "part_code", "supplier", "unit_price",
                "weight_source.kind", "weight_source.source_id", "weight_source.product_code", "pair_kind", "peer_item_id")
        paths = ", ".join(f"'$.{key}'" for key in keys)
        rows = self.connection.execute(f"""
            SELECT r.id, r.code, r.version, i.key + 1, json_extract(r.payload_json, '$.pair.peer_id'),
                   json_extract(i.value, {paths})
            FROM costing_records AS r, json_each(r.payload_json, '$.items') AS i
            WHERE trim(json_extract(i.value, '$.mode')) = 'weight'
            ORDER BY r.code, i.key
        """)
        return [{"record_id": key, "code": code, "version": version, "position": pos, "peer_id": peer,
                 **dict(zip(keys, json.loads(line), strict=True))} for key, code, version, pos, peer, line in rows]

    def get_many(self, record_ids: list[str]) -> list[dict]:
        if not record_ids:
            return []
        placeholders = ",".join("?" for _ in record_ids)
        rows = self.connection.execute(
            "SELECT id, payload_json FROM costing_records WHERE id IN (" + placeholders + ")", record_ids,
        ).fetchall()
        records = {row[0]: json.loads(row[1]) for row in rows}
        return [records[record_id] for record_id in record_ids]

    def insert(self, record: dict) -> None:
        try:
            self.connection.execute(
                """INSERT INTO costing_records
                (id,code,kind,name,brand,models,status,version,created_at,updated_at,payload_json)
                VALUES (:id,:code,:kind,:name,:brand,:models,:status,:version,:created_at,:updated_at,:payload_json)""",
                {**record, "payload_json": _json(record)},
            )
        except sqlite3.IntegrityError as exc:
            raise CostingConflict(f"型号 {record['code']} 已有成本单，不能重复新建。") from exc

    def update(self, record: dict, *, expected_version: int) -> bool:
        try:
            cursor = self.connection.execute(
                """UPDATE costing_records SET code=:code, kind=:kind, name=:name, brand=:brand,
                models=:models, status=:status, version=:version, updated_at=:updated_at, payload_json=:payload_json
                WHERE id=:id AND version=:expected_version""",
                {**record, "payload_json": _json(record), "expected_version": expected_version},
            )
            return cursor.rowcount == 1
        except sqlite3.IntegrityError as exc:
            raise CostingConflict(f"型号 {record['code']} 已有成本单，不能覆盖。") from exc

    def add_revision(self, before: dict | None, after: dict, *, actor: str, action: str) -> None:
        self.connection.execute(
            """INSERT INTO costing_revisions
            (record_id,version,action,actor,created_at,before_json,after_json) VALUES (?,?,?,?,?,?,?)""",
            (after["id"], after["version"], action, actor, after["updated_at"],
             _json(before) if before is not None else None, _json(after)),
        )

    def revisions(self, record_id: str) -> list[dict]:
        rows = self.connection.execute(
            """SELECT id,version,action,actor,created_at,before_json,after_json
            FROM costing_revisions WHERE record_id=? ORDER BY version DESC""", (record_id,),
        ).fetchall()
        return [
            {"id": row[0], "version": row[1], "action": row[2], "actor": row[3], "created_at": row[4],
             "before": json.loads(row[5]) if row[5] else None, "after": json.loads(row[6])}
            for row in rows
        ]

    def references(self) -> dict:
        row = self.connection.execute(
            "SELECT payload_json FROM costing_reference_data WHERE name='workbook'",
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def save_references(self, references: dict) -> None:
        self.connection.execute(
            """INSERT INTO costing_reference_data(name,payload_json) VALUES ('workbook',?)
            ON CONFLICT(name) DO UPDATE SET payload_json=excluded.payload_json""", (_json(references),),
        )


class SQLiteCostingUnitOfWork:
    def __init__(self, path: Path, *, write: bool = False) -> None:
        self.path = Path(path)
        self.write = write
        self.connection: sqlite3.Connection | None = None

    def __enter__(self):
        # mode=rw prevents service reads or startup from silently creating a database.
        self.connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("BEGIN IMMEDIATE" if self.write else "BEGIN")
        self.repository = SQLiteCostingRepository(self.connection)
        return self

    def commit(self) -> None:
        assert self.connection is not None
        self.connection.commit()

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self.connection is not None
        try:
            self.connection.rollback()
        finally:
            self.connection.close()
