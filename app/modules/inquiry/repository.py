from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from app.database import connect, log_event, now_text
from app.drawings import build_drawings_zip
from app.matcher import compact_text, normalize_code


class SQLiteInquiryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def audit(
        self,
        action: str,
        target_type: str,
        target_key: str,
        detail: str,
        *,
        actor: str,
    ) -> None:
        log_event(self.connection, action, target_type, target_key, detail, actor=actor)

    def save_alias(self, source_code: str, bld_no: str, note: str, *, actor: str) -> None:
        timestamp = now_text()
        key = normalize_code(source_code)
        before = self.connection.execute(
            "SELECT id FROM aliases WHERE source_code = ?",
            (key,),
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO aliases (source_code, bld_no, note, active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(source_code) DO UPDATE SET
              bld_no=excluded.bld_no, note=excluded.note, active=1, updated_at=excluded.updated_at
            """,
            (key, compact_text(bld_no), compact_text(note), timestamp, timestamp),
        )
        action = "新增人工映射" if before is None else "编辑人工映射"
        self.audit(action, "alias", key, f"{key} -> {compact_text(bld_no)}", actor=actor)

    def append_product_code(self, bld_no: str, code: str, target: str, *, actor: str) -> bool:
        product = self.connection.execute(
            "SELECT * FROM products WHERE bld_no = ?",
            (compact_text(bld_no),),
        ).fetchone()
        if product is None:
            return False
        value = compact_text(code)
        if not value:
            return False
        field = "oe_no_2" if target == "brand_code" else "oe_no_1"
        label = "品牌号码" if target == "brand_code" else "OE 号"
        existing = [line for line in str(product[field] or "").splitlines() if line.strip()]
        if normalize_code(value) in {normalize_code(line) for line in existing}:
            return False
        self.connection.execute(
            f"UPDATE products SET {field} = ?, updated_at = ? WHERE id = ?",
            ("\n".join(existing + [value]), now_text(), product["id"]),
        )
        self.audit(f"追加{label}", "product", product["bld_no"], f"{label}新增: {value}", actor=actor)
        return True

    def delete_alias(self, source_code: str, *, actor: str) -> None:
        key = normalize_code(source_code)
        self.connection.execute(
            "UPDATE aliases SET active = 0, updated_at = ? WHERE source_code = ?",
            (now_text(), key),
        )
        self.audit("删除人工映射", "alias", key, "", actor=actor)

    def build_drawings(self, rows: list[dict], output_path: Path) -> dict:
        return build_drawings_zip(self.connection, rows, output_path)


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteInquiryUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.connection: sqlite3.Connection | None = None
        self.repository: SQLiteInquiryRepository
        self._committed = False

    def __enter__(self) -> SQLiteInquiryUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteInquiryRepository(self.connection)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Inquiry unit of work is not active.")
        self.connection.commit()
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.connection is None:
            return
        if exc_type is not None or not self._committed:
            self.connection.rollback()
        self.connection.close()
        self.connection = None
