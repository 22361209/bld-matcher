from __future__ import annotations

from datetime import timedelta
from typing import Any

from .domain import JobNotFoundError, JobNotReadyError, JobRecord
from .repository import SQLiteJobRepository


class JobService:
    def __init__(self, repository: SQLiteJobRepository) -> None:
        self.repository = repository

    def submit(
        self,
        *,
        kind: str,
        owner_id: str,
        request_payload: dict[str, Any],
        progress: dict[str, Any] | None = None,
        max_attempts: int = 3,
        ttl: timedelta = timedelta(days=7),
    ) -> JobRecord:
        return self.repository.create(
            kind=kind,
            owner_id=owner_id,
            request_payload=request_payload,
            progress=progress,
            max_attempts=max_attempts,
            ttl=ttl,
        )

    def get(self, job_id: str, *, owner_id: str) -> JobRecord:
        job = self.repository.get_for_owner(job_id, owner_id)
        if job is None:
            raise JobNotFoundError("Job not found.")
        return job

    def cancel(self, job_id: str, *, owner_id: str, reason: str = "") -> JobRecord:
        job = self.repository.request_cancel(job_id, owner_id=owner_id, reason=reason)
        if job is None:
            raise JobNotFoundError("Job not found.")
        return job

    def result(self, job_id: str, *, owner_id: str) -> dict[str, Any]:
        job = self.get(job_id, owner_id=owner_id)
        if job.status != "completed":
            raise JobNotReadyError(job.status)
        return job.result
