from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.database import connect
from app.platform.audit_store import log_event

from .clock import datetime_text
from .runtime_config import RuntimeSettings


INQUIRY_UPLOAD_PREFIXES = ("inquiry-", "inquiry-text-")
MATERIAL_UPLOAD_PREFIXES = ("material-plan-", "material-data-")
INQUIRY_OUTPUT_PATTERN = re.compile(r"^re\d{6}-")
MATERIAL_OUTPUT_PATTERN = re.compile(r"料单(?:_\d+)?\.xlsx$")


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    generated_at: str
    files: dict[str, tuple[Path, ...]]
    artifact_ids: tuple[str, ...]
    job_ids: tuple[str, ...]
    idempotency_ids: tuple[int, ...]

    def summary(self) -> dict[str, object]:
        file_counts = {category: len(paths) for category, paths in self.files.items()}
        return {
            "generated_at": self.generated_at,
            "files": file_counts,
            "artifacts": len(self.artifact_ids),
            "jobs": len(self.job_ids),
            "idempotency_records": len(self.idempotency_ids),
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

        upload_files = tuple(
            path
            for path in self._expired_files(
                self.upload_root,
                current=current,
                retention_days_for=self._upload_retention_days,
            )
        )
        output_files = tuple(
            path
            for path in self._expired_files(
                self.output_root,
                current=current,
                retention_days_for=self._output_retention_days,
            )
            if path.resolve() not in active_artifact_paths
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

    def _expired_files(
        self,
        root: Path,
        *,
        current: datetime,
        retention_days_for: Callable[[Path], int],
    ) -> tuple[Path, ...]:
        expired: list[Path] = []
        for path in self._old_files(root, before=current):
            retention_days = retention_days_for(path)
            if retention_days == 0:
                continue
            try:
                if path.stat().st_mtime <= (current - timedelta(days=retention_days)).timestamp():
                    expired.append(path)
            except OSError:
                continue
        return tuple(expired)

    @staticmethod
    def _is_inquiry_upload(path: Path) -> bool:
        return path.name.startswith(INQUIRY_UPLOAD_PREFIXES)

    @staticmethod
    def _is_material_upload(path: Path) -> bool:
        return path.name.startswith(MATERIAL_UPLOAD_PREFIXES)

    @staticmethod
    def _is_inquiry_output(path: Path) -> bool:
        return bool(INQUIRY_OUTPUT_PATTERN.match(path.name)) or path.name.startswith("drawings-")

    def _upload_retention_days(self, path: Path) -> int:
        if self._is_inquiry_upload(path):
            return self.settings.inquiry_upload_retention_days
        if self._is_material_upload(path):
            return self.settings.material_upload_retention_days
        return self.settings.upload_retention_days

    def _output_retention_days(self, path: Path) -> int:
        if self._is_inquiry_output(path):
            return self.settings.inquiry_output_retention_days
        if MATERIAL_OUTPUT_PATTERN.search(path.name):
            return self.settings.material_output_retention_days
        if {"采购合同", "销售合同"}.intersection(path.relative_to(self.output_root).parts):
            return self.settings.contract_output_retention_days
        return self.settings.output_retention_days

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
