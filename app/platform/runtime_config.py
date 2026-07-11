from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_poll_seconds: float = Field(default=1, ge=0.05, le=30)
    job_lease_seconds: int = Field(default=900, ge=330, le=7200)
    worker_stale_seconds: int = Field(default=600, ge=15, le=3600)
    worker_heartbeat_seconds: int = Field(default=30, ge=1, le=300)
    require_worker_for_readiness: bool = True
    upload_retention_days: int = Field(default=30, ge=1, le=3650)
    output_retention_days: int = Field(default=30, ge=1, le=3650)
    job_retention_days: int = Field(default=7, ge=1, le=3650)
    backup_retention_days: int = Field(default=30, ge=1, le=3650)
    artifact_retention_hours: int = Field(default=24, ge=1, le=8760)
    idempotency_retention_hours: int = Field(default=24, ge=1, le=8760)
    ai_call_retention_days: int = Field(default=90, ge=1, le=3650)
    heartbeat_retention_days: int = Field(default=7, ge=1, le=365)
    api_key_rotation_days: int = Field(default=90, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_worker_timing(self) -> RuntimeSettings:
        if self.worker_heartbeat_seconds >= self.worker_stale_seconds:
            raise ValueError("BLD_WORKER_HEARTBEAT_SECONDS 必须小于 BLD_WORKER_STALE_SECONDS。")
        return self

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        return cls(
            job_poll_seconds=_float("BLD_JOB_POLL_SECONDS", 1),
            job_lease_seconds=_int("BLD_JOB_LEASE_SECONDS", 900),
            worker_stale_seconds=_int("BLD_WORKER_STALE_SECONDS", 600),
            worker_heartbeat_seconds=_int("BLD_WORKER_HEARTBEAT_SECONDS", 30),
            require_worker_for_readiness=_bool("BLD_REQUIRE_WORKER", True),
            upload_retention_days=_int("BLD_UPLOAD_RETENTION_DAYS", 30),
            output_retention_days=_int("BLD_OUTPUT_RETENTION_DAYS", 30),
            job_retention_days=_int("BLD_JOB_RETENTION_DAYS", 7),
            backup_retention_days=_int("BLD_BACKUP_RETENTION_DAYS", 30),
            artifact_retention_hours=_int("BLD_ARTIFACT_RETENTION_HOURS", 24),
            idempotency_retention_hours=_int("BLD_IDEMPOTENCY_RETENTION_HOURS", 24),
            ai_call_retention_days=_int("BLD_AI_CALL_RETENTION_DAYS", 90),
            heartbeat_retention_days=_int("BLD_HEARTBEAT_RETENTION_DAYS", 7),
            api_key_rotation_days=_int("BLD_API_KEY_ROTATION_DAYS", 90),
        )
