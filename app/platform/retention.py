from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.database import connect
from app.platform.audit_store import log_event

from .clock import datetime_text
from .runtime_config import RuntimeSettings


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    generated_at: str
    files: dict[str, tuple[Path, ...]]
    artifact_ids: tuple[str, ...]
    job_ids: tuple[str, ...]
    idempotency_ids: tuple[int, ...]
    ai_call_ids: tuple[int, ...]
    heartbeat_keys: tuple[tuple[str, str], ...]

    def summary(self) -> dict[str, object]:
        file_counts = {category: len(paths) for category, paths in self.files.items()}
        return {
            "generated_at": self.generated_at,
            "files": file_counts,
            "artifacts": len(self.artifact_ids),
            "jobs": len(self.job_ids),
            "idempotency_records": len(self.idempotency_ids),
            "ai_call_records": len(self.ai_call_ids),
            "heartbeat_records": len(self.heartbeat_keys),
            "total_files": sum(file_counts.values()),
        }


class RuntimeRetentionService:
    def __init__(
        self,
        database_path: Path,
        *,
        upload_root: Path,
        output_root: Path,
        backup_roots: tuple[Path, ...],
        settings: RuntimeSettings,
    ) -> None:
        self.database_path = database_path
        self.upload_root = upload_root.resolve()
        self.output_root = output_root.resolve()
        self.backup_roots = tuple(root.resolve() for root in backup_roots)
        self.settings = settings

    def build_plan(self, *, now: datetime | None = None) -> RetentionPlan:
        current = now or datetime.now()
        stamp = datetime_text(current)
        with connect(self.database_path) as connection:
            active_artifact_paths = {
                Path(str(row["storage_path"])).resolve()
                for row in connection.execute(
                    "SELECT storage_path FROM api_artifacts WHERE expires_at > ?",
                    (stamp,),
                ).fetchall()
                if str(row["storage_path"] or "")
            }
            expired_artifacts = connection.execute(
                "SELECT id, storage_path FROM api_artifacts WHERE expires_at <= ?",
                (stamp,),
            ).fetchall()
            artifact_ids = tuple(str(row["id"]) for row in expired_artifacts)
            artifact_files = tuple(
                path
                for row in expired_artifacts
                if (path := self._allowed_file(Path(str(row["storage_path"])), (self.output_root,))) is not None
                and path not in active_artifact_paths
            )
            job_ids = tuple(
                str(row["id"])
                for row in connection.execute(
                    """
                    SELECT id FROM background_jobs
                    WHERE status IN ('completed', 'failed', 'cancelled') AND expires_at <= ?
                    """,
                    (stamp,),
                ).fetchall()
            )
            idempotency_ids = tuple(
                int(row["id"])
                for row in connection.execute(
                    "SELECT id FROM api_idempotency_keys WHERE expires_at <= ?",
                    (stamp,),
                ).fetchall()
            )
            ai_cutoff = datetime_text(current - timedelta(days=self.settings.ai_call_retention_days))
            ai_call_ids = tuple(
                int(row["id"])
                for row in connection.execute(
                    "SELECT id FROM ai_provider_calls WHERE created_at <= ?",
                    (ai_cutoff,),
                ).fetchall()
            )
            heartbeat_cutoff = datetime_text(current - timedelta(days=self.settings.heartbeat_retention_days))
            heartbeat_keys = tuple(
                (str(row["component"]), str(row["instance_id"]))
                for row in connection.execute(
                    "SELECT component, instance_id FROM runtime_heartbeats WHERE updated_at <= ?",
                    (heartbeat_cutoff,),
                ).fetchall()
            )
            protected_job_paths = self._protected_job_paths(connection)

        upload_files = tuple(
            path
            for path in self._old_files(
                self.upload_root,
                before=current - timedelta(days=self.settings.upload_retention_days),
            )
            if not self._is_protected(path, protected_job_paths)
        )
        output_files = tuple(
            path
            for path in self._old_files(
                self.output_root,
                before=current - timedelta(days=self.settings.output_retention_days),
            )
            if path.resolve() not in active_artifact_paths and not self._is_protected(path, protected_job_paths)
        )
        backup_files = tuple(
            path
            for root in self.backup_roots
            for path in self._old_files(
                root,
                before=current - timedelta(days=self.settings.backup_retention_days),
            )
        )
        files = {
            "uploads": self._deduplicate(upload_files),
            "outputs": self._deduplicate((*output_files, *artifact_files)),
            "backups": self._deduplicate(backup_files),
        }
        return RetentionPlan(
            generated_at=stamp,
            files=files,
            artifact_ids=artifact_ids,
            job_ids=job_ids,
            idempotency_ids=idempotency_ids,
            ai_call_ids=ai_call_ids,
            heartbeat_keys=heartbeat_keys,
        )

    def apply(self, plan: RetentionPlan, *, actor: str = "runtime-cleanup") -> dict[str, object]:
        removed_files = 0
        for paths in plan.files.values():
            for path in paths:
                allowed = self._allowed_file(path, (self.upload_root, self.output_root, *self.backup_roots))
                if allowed is None:
                    continue
                try:
                    allowed.unlink(missing_ok=True)
                    removed_files += 1
                except OSError:
                    continue
        self._remove_empty_directories((self.upload_root, self.output_root, *self.backup_roots))

        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._delete_ids(connection, "api_artifacts", "id", plan.artifact_ids)
            if plan.job_ids:
                self._delete_ids(connection, "background_job_events", "job_id", plan.job_ids)
                self._delete_ids(connection, "background_jobs", "id", plan.job_ids)
            self._delete_ids(connection, "api_idempotency_keys", "id", plan.idempotency_ids)
            self._delete_ids(connection, "ai_provider_calls", "id", plan.ai_call_ids)
            for component, instance_id in plan.heartbeat_keys:
                connection.execute(
                    "DELETE FROM runtime_heartbeats WHERE component = ? AND instance_id = ?",
                    (component, instance_id),
                )
            summary = plan.summary()
            summary["removed_files"] = removed_files
            log_event(
                connection,
                "执行运行数据保留期清理",
                "runtime_retention",
                plan.generated_at,
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
                actor=actor,
            )
            connection.commit()
        return summary

    @staticmethod
    def _delete_ids(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        values: tuple[object, ...],
    ) -> None:
        if not values:
            return
        placeholders = ", ".join("?" for _ in values)
        connection.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", values)

    @staticmethod
    def _old_files(root: Path, *, before: datetime) -> tuple[Path, ...]:
        if not root.is_dir():
            return ()
        cutoff = before.timestamp()
        files: list[Path] = []
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime <= cutoff:
                    files.append(path)
            except OSError:
                continue
        return tuple(files)

    def _protected_job_paths(self, connection: sqlite3.Connection) -> tuple[Path, ...]:
        protected: set[Path] = set()
        rows = connection.execute(
            "SELECT request_payload FROM background_jobs WHERE status IN ('queued', 'running')"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["request_payload"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                continue
            values = payload.get("protected_paths") if isinstance(payload, dict) else None
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    continue
                path = self._allowed_path(Path(value), (self.upload_root, self.output_root))
                if path is not None:
                    protected.add(path)
        return tuple(sorted(protected, key=lambda path: path.as_posix()))

    @staticmethod
    def _is_protected(path: Path, protected_paths: tuple[Path, ...]) -> bool:
        absolute = path.resolve(strict=False)
        return any(protected == absolute or protected in absolute.parents for protected in protected_paths)

    @staticmethod
    def _allowed_path(path: Path, roots: tuple[Path, ...]) -> Path | None:
        absolute = path.expanduser().resolve(strict=False)
        for root in roots:
            root_absolute = root.resolve(strict=False)
            if root_absolute == absolute or root_absolute in absolute.parents:
                return absolute
        return None

    @staticmethod
    def _allowed_file(path: Path, roots: tuple[Path, ...]) -> Path | None:
        absolute = path.expanduser().resolve(strict=False)
        for root in roots:
            root_absolute = root.resolve(strict=False)
            if root_absolute in absolute.parents:
                return absolute
        return None

    @staticmethod
    def _deduplicate(paths: tuple[Path, ...]) -> tuple[Path, ...]:
        return tuple(sorted({path.absolute() for path in paths}, key=lambda path: path.as_posix()))

    @staticmethod
    def _remove_empty_directories(roots: tuple[Path, ...]) -> None:
        for root in roots:
            if not root.is_dir():
                continue
            directories = sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True)
            for directory in directories:
                try:
                    directory.rmdir()
                except OSError:
                    continue
