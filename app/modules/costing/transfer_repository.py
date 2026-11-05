"""Transactional persistence for preserving an accepted cost workspace verbatim."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.platform.audit_store import log_event

from .domain import CostingConflict, CostingValidationError
from .repository import SQLiteCostingRepository


RECORD_COLUMNS = ("id", "code", "kind", "name", "brand", "models", "status", "version", "created_at", "updated_at")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class CostingTransferRepository:
    def __init__(self, path: Path, *, write: bool = False) -> None:
        self.path = Path(path)
        self.write = write
        self.connection: sqlite3.Connection | None = None

    def __enter__(self):
        mode = "rw" if self.write else "ro"
        self.connection = sqlite3.connect(self.path.resolve().as_uri() + f"?mode={mode}", uri=True, timeout=15)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("BEGIN IMMEDIATE" if self.write else "BEGIN")
        return self

    def read_content(self) -> dict:
        assert self.connection is not None
        records = []
        rows = self.connection.execute(
            "SELECT " + ",".join(RECORD_COLUMNS) + ",payload_json FROM costing_records ORDER BY id",
        ).fetchall()
        for row in rows:
            record = json.loads(row[-1])
            if not isinstance(record, dict) or any(record.get(key) != row[index] for index, key in enumerate(RECORD_COLUMNS)):
                raise CostingValidationError("成本索引与记录内容不一致，请先核对资料。")
            records.append(record)
        revisions = [
            {"id": row[0], "record_id": row[1], "version": row[2], "action": row[3], "actor": row[4],
             "created_at": row[5], "before": json.loads(row[6]) if row[6] is not None else None,
             "after": json.loads(row[7])}
            for row in self.connection.execute(
                "SELECT id,record_id,version,action,actor,created_at,before_json,after_json FROM costing_revisions ORDER BY id",
            )
        ]
        references = self.connection.execute("SELECT name,payload_json FROM costing_reference_data").fetchall()
        if len(references) > 1 or references and references[0][0] != "workbook":
            raise CostingValidationError("成本参考资料格式无效。")
        reference_data = json.loads(references[0][1]) if references else None
        if references and not isinstance(reference_data, dict):
            raise CostingValidationError("已有成本参考行内容无效，不能当作空资料覆盖。")
        return {"format_version": 1, "records": records, "revisions": revisions,
                "references": reference_data}

    def restore(self, content: dict) -> None:
        assert self.connection is not None
        repository = SQLiteCostingRepository(self.connection)
        try:
            for record in content["records"]:
                repository.insert(record)
            for revision in content["revisions"]:
                self.connection.execute(
                    """INSERT INTO costing_revisions
                    (id,record_id,version,action,actor,created_at,before_json,after_json)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (revision["id"], revision["record_id"], revision["version"], revision["action"], revision["actor"],
                     revision["created_at"], _json(revision["before"]) if revision["before"] is not None else None,
                     _json(revision["after"])),
                )
            if content["references"] is not None:
                repository.save_references(content["references"])
        except sqlite3.IntegrityError as exc:
            raise CostingConflict("成本迁移数据存在重复或关联冲突，未写入任何资料。") from exc

    def audit(self, *, actor: str, digest: str, records: int, revisions: int) -> None:
        assert self.connection is not None
        if self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_logs'").fetchone():
            log_event(self.connection, "接入成本试用资料", "costing_transfer", digest,
                      detail=f"保留 {records} 个型号、{revisions} 条修订及原始来源；未覆盖其他业务资料。", actor=actor)

    def commit(self) -> None:
        assert self.connection is not None
        self.connection.commit()

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self.connection is not None
        try:
            self.connection.rollback()
        finally:
            self.connection.close()
