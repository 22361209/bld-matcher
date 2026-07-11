from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .domain import JobCancelledError, JobInterruptedError, JobRecord
from .repository import SQLiteJobRepository


logger = logging.getLogger(__name__)


class JobHandler(Protocol):
    def execute(self, job: JobRecord, context: JobExecutionContext) -> dict[str, Any]: ...


class JobExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class JobExecutionContext:
    repository: SQLiteJobRepository
    job_id: str
    worker_id: str
    lease_seconds: int
    should_stop: Callable[[], bool]
    heartbeat: Callable[[dict[str, Any]], None]

    def update(self, progress: dict[str, Any]) -> None:
        if self.should_stop():
            raise JobInterruptedError("Worker shutdown was requested.")
        updated = self.repository.update_progress(
            self.job_id,
            worker_id=self.worker_id,
            progress=progress,
            lease_seconds=self.lease_seconds,
        )
        if updated is None:
            raise JobExecutionError("job.lease_lost", "任务执行权已失效，请重试。")
        if updated.cancel_requested:
            raise JobCancelledError("Job cancellation was requested.")
        self.heartbeat({"state": "running", "job_id": self.job_id})

    def cancel_requested(self) -> bool:
        requested = self.repository.checkpoint(
            self.job_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if requested is None:
            raise JobExecutionError("job.lease_lost", "任务执行权已失效，请重试。")
        return requested

    def check_cancelled(self) -> None:
        if self.should_stop():
            raise JobInterruptedError("Worker shutdown was requested.")
        if self.cancel_requested():
            raise JobCancelledError("Job cancellation was requested.")


class PersistentJobWorker:
    def __init__(
        self,
        repository: SQLiteJobRepository,
        handlers: Mapping[str, JobHandler],
        *,
        worker_id: str | None = None,
        lease_seconds: int = 300,
        poll_seconds: float = 1.0,
        heartbeat_interval_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repository = repository
        self.handlers = dict(handlers)
        self.worker_id = worker_id or f"{socket.gethostname()}-{time.time_ns()}"
        self.lease_seconds = max(30, int(lease_seconds))
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.heartbeat_interval_seconds = max(1.0, float(heartbeat_interval_seconds))
        self.monotonic = monotonic
        self._last_heartbeat_at: float | None = None
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def _heartbeat(self, metadata: dict[str, Any], *, force: bool = False) -> None:
        now = self.monotonic()
        if not force and self._last_heartbeat_at is not None:
            if now - self._last_heartbeat_at < self.heartbeat_interval_seconds:
                return
        try:
            self.repository.heartbeat(self.worker_id, metadata=metadata)
        except Exception:
            logger.exception("Worker heartbeat update failed", extra={"job_id": metadata.get("job_id")})
        self._last_heartbeat_at = now

    def run_once(self, *, job_id: str | None = None) -> JobRecord | None:
        if self._stop_requested:
            self._heartbeat({"state": "stopping"}, force=True)
            return None
        job = self.repository.claim_next(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            job_id=job_id,
        )
        if job is None:
            self._heartbeat({"state": "idle"})
            return None
        handler = self.handlers.get(job.kind)
        if handler is None:
            logger.error("No job handler is registered", extra={"job_id": job.id, "job_kind": job.kind})
            failed = self.repository.fail(
                job.id,
                worker_id=self.worker_id,
                error_code="job.unsupported_kind",
                error_message="当前任务类型没有可用执行器。",
            )
            self._heartbeat({"state": "idle"}, force=True)
            return failed

        context = JobExecutionContext(
            repository=self.repository,
            job_id=job.id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            should_stop=lambda: self._stop_requested,
            heartbeat=lambda metadata: self._heartbeat(metadata, force=True),
        )
        self._heartbeat({"state": "running", "job_id": job.id}, force=True)
        try:
            result = handler.execute(job, context)
            completed = self.repository.complete(job.id, worker_id=self.worker_id, result=result)
        except JobCancelledError:
            completed = self.repository.mark_cancelled(job.id, worker_id=self.worker_id)
        except JobInterruptedError:
            completed = self.repository.requeue_interrupted(job.id, worker_id=self.worker_id)
        except JobExecutionError as exc:
            logger.warning(
                "Job execution failed with a stable error",
                extra={"job_id": job.id, "job_kind": job.kind, "error_code": exc.code},
            )
            completed = self.repository.fail(
                job.id,
                worker_id=self.worker_id,
                error_code=exc.code,
                error_message=exc.message,
            )
        except Exception:
            logger.exception("Unhandled job execution error", extra={"job_id": job.id, "job_kind": job.kind})
            completed = self.repository.fail(
                job.id,
                worker_id=self.worker_id,
                error_code="job.unexpected",
                error_message="任务执行失败，请稍后重试。",
            )
        self._heartbeat({"state": "stopping" if self._stop_requested else "idle"}, force=True)
        return completed

    def run_forever(self, *, should_stop: Callable[[], bool]) -> None:
        while not self._stop_requested and not should_stop():
            processed = self.run_once()
            if processed is None:
                time.sleep(self.poll_seconds)
