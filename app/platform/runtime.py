from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.migrations import MIGRATIONS

from .runtime_config import RuntimeSettings


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, dict[str, object]]

    def payload(self) -> dict[str, object]:
        return {"status": "ready" if self.ready else "not_ready", "checks": self.checks}


class RuntimeHealthService:
    def __init__(self, database_path: Path, settings: RuntimeSettings) -> None:
        self.database_path = database_path
        self.settings = settings

    def readiness(self, *, now: datetime | None = None) -> ReadinessReport:
        current = now or datetime.now()
        del current  # 保留参数以兼容既有调用签名
        checks: dict[str, dict[str, object]] = {
            "database": {"ok": False},
            "migrations": {"ok": False, "expected": MIGRATIONS[-1][0]},
            "business_probe": {"ok": False},
        }
        try:
            connection = self._read_only_connection()
        except (OSError, sqlite3.Error):
            checks["database"] = {"ok": False, "reason": "unavailable"}
            return ReadinessReport(ready=False, checks=checks)
        try:
            connection.execute("SELECT 1").fetchone()
            checks["database"] = {"ok": True}
            try:
                latest = connection.execute(
                    "SELECT id FROM schema_migrations ORDER BY applied_at DESC, id DESC LIMIT 1"
                ).fetchone()
                latest_id = str(latest["id"]) if latest else ""
                expected_ids = {migration_id for migration_id, _migration in MIGRATIONS}
                applied_ids = {
                    str(row["id"]) for row in connection.execute("SELECT id FROM schema_migrations").fetchall()
                }
                checks["migrations"] = {
                    "ok": expected_ids.issubset(applied_ids),
                    "expected": MIGRATIONS[-1][0],
                    "latest": latest_id,
                }
            except sqlite3.Error:
                checks["migrations"] = {
                    "ok": False,
                    "expected": MIGRATIONS[-1][0],
                    "reason": "missing_or_unreadable",
                }
            try:
                admin = connection.execute(
                    "SELECT 1 FROM users WHERE role = 'admin' AND active = 1 LIMIT 1"
                ).fetchone()
                connection.execute("SELECT COUNT(*) FROM products").fetchone()
                checks["business_probe"] = {"ok": bool(admin)}
            except sqlite3.Error:
                checks["business_probe"] = {"ok": False, "reason": "missing_or_unreadable"}
        except sqlite3.Error:
            checks["database"] = {"ok": False, "reason": "unreadable"}
        finally:
            connection.close()
        ready = all(bool(item.get("ok")) for item in checks.values())
        return ReadinessReport(ready=ready, checks=checks)

    def _read_only_connection(self) -> sqlite3.Connection:
        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection
