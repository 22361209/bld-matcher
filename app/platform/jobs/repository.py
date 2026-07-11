from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.database import connect
from app.platform.clock import datetime_text, now_text

from .domain import JobRecord, TERMINAL_JOB_STATUSES


ConnectionFactory = Callable[[Path], sqlite3.Connection]


def _json_object(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _record(row: sqlite3.Row | None) -> JobRecord | None:
    if row is None:
        return None
    return JobRecord(
        id=str(row["id"]),
        kind=str(row["kind"]),
        owner_id=str(row["owner_id"]),
        status=str(row["status"]),
        request_payload=_json_object(row["request_payload"]),
        progress=_json_object(row["progress_payload"]),
        result=_json_object(row["result_payload"]),
        error_code=str(row["error_code"] or ""),
        error_message=str(row["error_message"] or ""),
        cancel_requested=bool(row["cancel_requested"]),
        attempt=int(row["attempt"] or 0),
        max_attempts=int(row["max_attempts"] or 1),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
        started_at=str(row["started_at"] or ""),
        finished_at=str(row["finished_at"] or ""),
        expires_at=str(row["expires_at"] or ""),
    )


class SQLiteJobRepository:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory

    def create(
        self,
        *,
        kind: str,
        owner_id: str,
        request_payload: dict[str, Any],
        progress: dict[str, Any] | None = None,
        max_attempts: int = 3,
        ttl: timedelta = timedelta(days=7),
    ) -> JobRecord:
        job_id = f"job_{secrets.token_urlsafe(18)}"
        now = datetime.now()
        stamp = datetime_text(now)
        expires_at = datetime_text(now + ttl)
        with self.connection_factory(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO background_jobs (
                  id, kind, owner_id, status, request_payload, progress_payload,
                  result_payload, error_code, error_message, cancel_requested,
                  attempt, max_attempts, run_after, lease_owner, lease_expires_at,
                  created_at, updated_at, started_at, finished_at, expires_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, '{}', '', '', 0, 0, ?, ?, '', '', ?, ?, '', '', ?)
                """,
                (
                    job_id,
                    kind,
                    owner_id,
                    json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(progress or {}, ensure_ascii=False, separators=(",", ":")),
                    max(1, min(10, int(max_attempts))),
                    stamp,
                    stamp,
                    stamp,
                    expires_at,
                ),
            )
            self._event(connection, job_id, "queued", progress or {})
            connection.commit()
            return self._get(connection, job_id)

    def get(self, job_id: str) -> JobRecord | None:
        with self.connection_factory(self.database_path) as connection:
            row = connection.execute("SELECT * FROM background_jobs WHERE id = ?", (job_id,)).fetchone()
        return _record(row)

    def get_for_owner(self, job_id: str, owner_id: str) -> JobRecord | None:
        with self.connection_factory(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM background_jobs WHERE id = ? AND owner_id = ?",
                (job_id, owner_id),
            ).fetchone()
        return _record(row)

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        job_id: str | None = None,
    ) -> JobRecord | None:
        now = datetime.now()
        stamp = datetime_text(now)
        lease_expires_at = datetime_text(now + timedelta(seconds=max(30, lease_seconds)))
        with self.connection_factory(self.database_path) as connection:
            if not self._has_claimable_work(connection, stamp=stamp, job_id=job_id):
                return None
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired_leases(connection, stamp)
            sql = """
                SELECT id
                FROM background_jobs
                WHERE status = 'queued' AND cancel_requested = 0 AND run_after <= ?
            """
            params: list[object] = [stamp]
            if job_id:
                sql += " AND id = ?"
                params.append(job_id)
            sql += " ORDER BY created_at, id LIMIT 1"
            row = connection.execute(sql, params).fetchone()
            if row is None:
                connection.commit()
                return None
            claimed_id = str(row["id"])
            cursor = connection.execute(
                """
                UPDATE background_jobs
                SET status = 'running', attempt = attempt + 1, lease_owner = ?,
                    lease_expires_at = ?, started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                    updated_at = ?
                WHERE id = ? AND status = 'queued' AND cancel_requested = 0
                """,
                (worker_id, lease_expires_at, stamp, stamp, claimed_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            self._event(connection, claimed_id, "running", {"worker_id": worker_id})
            connection.commit()
            return self._get(connection, claimed_id)

    def update_progress(
        self,
        job_id: str,
        *,
        worker_id: str,
        progress: dict[str, Any],
        lease_seconds: int,
    ) -> JobRecord | None:
        now = datetime.now()
        stamp = datetime_text(now)
        lease_expires_at = datetime_text(now + timedelta(seconds=max(30, lease_seconds)))
        with self.connection_factory(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE background_jobs
                SET progress_payload = ?, updated_at = ?, lease_expires_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (
                    json.dumps(progress, ensure_ascii=False, separators=(",", ":")),
                    stamp,
                    lease_expires_at,
                    job_id,
                    worker_id,
                ),
            )
            if cursor.rowcount:
                self._event(connection, job_id, "progress", progress)
            connection.commit()
            return self._get(connection, job_id) if cursor.rowcount else None

    def checkpoint(self, job_id: str, *, worker_id: str, lease_seconds: int) -> bool | None:
        lease_expires_at = datetime_text(datetime.now() + timedelta(seconds=max(30, lease_seconds)))
        with self.connection_factory(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE background_jobs SET lease_expires_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (lease_expires_at, job_id, worker_id),
            )
            if cursor.rowcount != 1:
                connection.commit()
                return None
            row = connection.execute(
                """
                SELECT cancel_requested
                FROM background_jobs
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (job_id, worker_id),
            ).fetchone()
            connection.commit()
        return bool(row and row["cancel_requested"])

    def request_cancel(self, job_id: str, *, owner_id: str, reason: str = "") -> JobRecord | None:
        stamp = now_text()
        with self.connection_factory(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM background_jobs WHERE id = ? AND owner_id = ?",
                (job_id, owner_id),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            status = str(row["status"])
            if status == "queued":
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'cancelled', cancel_requested = 1, updated_at = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (stamp, stamp, job_id),
                )
                self._event(connection, job_id, "cancelled", {"reason": reason})
            elif status == "running":
                connection.execute(
                    "UPDATE background_jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                    (stamp, job_id),
                )
                self._event(connection, job_id, "cancel_requested", {"reason": reason})
            connection.commit()
            return self._get(connection, job_id)

    def complete(self, job_id: str, *, worker_id: str, result: dict[str, Any]) -> JobRecord | None:
        stamp = now_text()
        with self.connection_factory(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE background_jobs SET status = 'completed', result_payload = ?,
                    error_code = '', error_message = '', cancel_requested = 0,
                    updated_at = ?, finished_at = ?, lease_owner = '', lease_expires_at = ''
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    stamp,
                    stamp,
                    job_id,
                    worker_id,
                ),
            )
            if cursor.rowcount:
                self._event(connection, job_id, "completed", result)
            connection.commit()
            return self._get(connection, job_id) if cursor.rowcount else None

    def requeue_interrupted(self, job_id: str, *, worker_id: str) -> JobRecord | None:
        stamp = now_text()
        with self.connection_factory(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT cancel_requested FROM background_jobs
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (job_id, worker_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            if bool(row["cancel_requested"]):
                connection.execute(
                    """
                    UPDATE background_jobs SET status = 'cancelled', updated_at = ?, finished_at = ?,
                        lease_owner = '', lease_expires_at = ''
                    WHERE id = ? AND status = 'running' AND lease_owner = ?
                    """,
                    (stamp, stamp, job_id, worker_id),
                )
                self._event(connection, job_id, "cancelled", {"reason": "worker_stopping"})
            else:
                connection.execute(
                    """
                    UPDATE background_jobs SET status = 'queued', run_after = ?, updated_at = ?,
                        attempt = CASE WHEN attempt > 0 THEN attempt - 1 ELSE 0 END,
                        lease_owner = '', lease_expires_at = ''
                    WHERE id = ? AND status = 'running' AND lease_owner = ?
                    """,
                    (stamp, stamp, job_id, worker_id),
                )
                self._event(connection, job_id, "requeued", {"reason": "worker_stopping"})
            connection.commit()
            return self._get(connection, job_id)

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        error_code: str,
        error_message: str,
    ) -> JobRecord | None:
        stamp = now_text()
        with self.connection_factory(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE background_jobs
                SET status = 'failed', error_code = ?, error_message = ?, updated_at = ?,
                    finished_at = ?, lease_owner = '', lease_expires_at = ''
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (error_code, error_message, stamp, stamp, job_id, worker_id),
            )
            if cursor.rowcount:
                self._event(connection, job_id, "failed", {"error_code": error_code})
            connection.commit()
            return self._get(connection, job_id) if cursor.rowcount else None

    def mark_cancelled(self, job_id: str, *, worker_id: str) -> JobRecord | None:
        stamp = now_text()
        with self.connection_factory(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE background_jobs
                SET status = 'cancelled', cancel_requested = 1, updated_at = ?, finished_at = ?,
                    lease_owner = '', lease_expires_at = ''
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (stamp, stamp, job_id, worker_id),
            )
            if cursor.rowcount:
                self._event(connection, job_id, "cancelled", {})
            connection.commit()
            return self._get(connection, job_id) if cursor.rowcount else None

    def heartbeat(self, worker_id: str, *, metadata: dict[str, Any] | None = None) -> None:
        stamp = now_text()
        with self.connection_factory(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO runtime_heartbeats (component, instance_id, payload, updated_at)
                VALUES ('worker', ?, ?, ?)
                ON CONFLICT(component, instance_id) DO UPDATE
                SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (
                    worker_id,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                    stamp,
                ),
            )
            connection.commit()

    def _recover_expired_leases(self, connection: sqlite3.Connection, stamp: str) -> None:
        expired = connection.execute(
            """
            SELECT id, cancel_requested, attempt, max_attempts
            FROM background_jobs
            WHERE status = 'running' AND lease_expires_at != '' AND lease_expires_at <= ?
            """,
            (stamp,),
        ).fetchall()
        for row in expired:
            job_id = str(row["id"])
            if bool(row["cancel_requested"]):
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'cancelled', updated_at = ?, finished_at = ?, lease_owner = '', lease_expires_at = ''
                    WHERE id = ?
                    """,
                    (stamp, stamp, job_id),
                )
                self._event(connection, job_id, "cancelled", {"reason": "expired_lease"})
            elif int(row["attempt"]) < int(row["max_attempts"]):
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'queued', updated_at = ?, run_after = ?, lease_owner = '', lease_expires_at = ''
                    WHERE id = ?
                    """,
                    (stamp, stamp, job_id),
                )
                self._event(connection, job_id, "requeued", {"reason": "expired_lease"})
            else:
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'failed', error_code = 'job.lease_expired',
                        error_message = '任务执行中断且已达到恢复次数上限。', updated_at = ?,
                        finished_at = ?, lease_owner = '', lease_expires_at = ''
                    WHERE id = ?
                    """,
                    (stamp, stamp, job_id),
                )
                self._event(connection, job_id, "failed", {"error_code": "job.lease_expired"})

    @staticmethod
    def _has_claimable_work(connection: sqlite3.Connection, *, stamp: str, job_id: str | None) -> bool:
        queued_sql = """
            SELECT 1 FROM background_jobs
            WHERE status = 'queued' AND cancel_requested = 0 AND run_after <= ?
        """
        expired_sql = """
            SELECT 1 FROM background_jobs
            WHERE status = 'running' AND lease_expires_at != '' AND lease_expires_at <= ?
        """
        queued_params: list[object] = [stamp]
        expired_params: list[object] = [stamp]
        if job_id:
            queued_sql += " AND id = ?"
            expired_sql += " AND id = ?"
            queued_params.append(job_id)
            expired_params.append(job_id)
        return bool(
            connection.execute(queued_sql + " LIMIT 1", queued_params).fetchone()
            or connection.execute(expired_sql + " LIMIT 1", expired_params).fetchone()
        )

    @staticmethod
    def _event(connection: sqlite3.Connection, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        connection.execute(
            """
            INSERT INTO background_job_events (job_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                job_id,
                event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now_text(),
            ),
        )

    @staticmethod
    def _get(connection: sqlite3.Connection, job_id: str) -> JobRecord:
        record = _record(connection.execute("SELECT * FROM background_jobs WHERE id = ?", (job_id,)).fetchone())
        if record is None:
            raise RuntimeError("Persisted job could not be reloaded.")
        return record

    def purge_terminal(self, *, before: str) -> int:
        placeholders = ", ".join("?" for _ in TERMINAL_JOB_STATUSES)
        params = [*sorted(TERMINAL_JOB_STATUSES), before]
        with self.connection_factory(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT id FROM background_jobs WHERE status IN ({placeholders}) AND expires_at <= ?",
                params,
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            if ids:
                id_placeholders = ", ".join("?" for _ in ids)
                connection.execute(f"DELETE FROM background_job_events WHERE job_id IN ({id_placeholders})", ids)
                connection.execute(f"DELETE FROM background_jobs WHERE id IN ({id_placeholders})", ids)
            connection.commit()
        return len(ids)
