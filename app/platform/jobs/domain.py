from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})


class JobNotFoundError(LookupError):
    pass


class JobNotReadyError(RuntimeError):
    def __init__(self, status: str) -> None:
        super().__init__(f"Job result is not ready: {status}")
        self.status = status


class JobCancelledError(RuntimeError):
    pass


class JobInterruptedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    kind: str
    owner_id: str
    status: str
    request_payload: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    cancel_requested: bool = False
    attempt: int = 0
    max_attempts: int = 1
    created_at: str = ""
    updated_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    expires_at: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    def public_payload(self, *, include_result: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "cancel_requested": self.cancel_requested,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "expires_at": self.expires_at,
        }
        if include_result and self.status == "completed":
            payload["result"] = self.result
        if self.status == "failed":
            payload["error"] = {
                "code": self.error_code or "job.failed",
                "message": self.error_message or "任务执行失败。",
            }
        return payload
