from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_retention_days: int = Field(default=30, ge=1, le=3650)
    output_retention_days: int = Field(default=30, ge=1, le=3650)
    inquiry_upload_retention_days: int = Field(default=0, ge=0, le=3650)
    inquiry_output_retention_days: int = Field(default=0, ge=0, le=3650)
    material_upload_retention_days: int = Field(default=0, ge=0, le=3650)
    material_output_retention_days: int = Field(default=0, ge=0, le=3650)
    contract_output_retention_days: int = Field(default=0, ge=0, le=3650)
    backup_retention_days: int = Field(default=30, ge=1, le=3650)
    artifact_retention_hours: int = Field(default=24, ge=1, le=8760)
    idempotency_retention_hours: int = Field(default=24, ge=1, le=8760)
    api_key_rotation_days: int = Field(default=90, ge=1, le=3650)

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        return cls(
            upload_retention_days=_int("BLD_UPLOAD_RETENTION_DAYS", 30),
            output_retention_days=_int("BLD_OUTPUT_RETENTION_DAYS", 30),
            inquiry_upload_retention_days=_int("BLD_INQUIRY_UPLOAD_RETENTION_DAYS", 0),
            inquiry_output_retention_days=_int("BLD_INQUIRY_OUTPUT_RETENTION_DAYS", 0),
            material_upload_retention_days=_int("BLD_MATERIAL_UPLOAD_RETENTION_DAYS", 0),
            material_output_retention_days=_int("BLD_MATERIAL_OUTPUT_RETENTION_DAYS", 0),
            contract_output_retention_days=_int("BLD_CONTRACT_OUTPUT_RETENTION_DAYS", 0),
            backup_retention_days=_int("BLD_BACKUP_RETENTION_DAYS", 30),
            artifact_retention_hours=_int("BLD_ARTIFACT_RETENTION_HOURS", 24),
            idempotency_retention_hours=_int("BLD_IDEMPOTENCY_RETENTION_HOURS", 24),
            api_key_rotation_days=_int("BLD_API_KEY_ROTATION_DAYS", 90),
        )
