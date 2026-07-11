from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.platform.api_schemas import StrictApiModel


class JobCancelRequest(StrictApiModel):
    reason: str = Field(default="", max_length=200)


class JobErrorData(StrictApiModel):
    code: str
    message: str


class JobPublicData(StrictApiModel):
    id: str
    kind: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: dict[str, Any] = Field(default_factory=dict)
    cancel_requested: bool
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    created_at: str
    updated_at: str
    started_at: str
    finished_at: str
    expires_at: str
    result: dict[str, Any] | None = None
    error: JobErrorData | None = None


class JobData(StrictApiModel):
    job: JobPublicData


class JobEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: JobData
    warnings: list[str] = Field(default_factory=list)


class JobResultData(StrictApiModel):
    job_id: str
    result: dict[str, Any]


class JobResultEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: JobResultData
    warnings: list[str] = Field(default_factory=list)
