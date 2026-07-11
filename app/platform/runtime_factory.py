from __future__ import annotations

from functools import lru_cache

from app.config import DATA_DIR, DB_PATH, OUTPUT_DIR, UPLOAD_DIR

from .retention import RuntimeRetentionService
from .runtime import RuntimeHealthService
from .runtime_config import RuntimeSettings


@lru_cache(maxsize=1)
def get_runtime_settings() -> RuntimeSettings:
    return RuntimeSettings.from_environment()


@lru_cache(maxsize=1)
def get_runtime_health_service() -> RuntimeHealthService:
    return RuntimeHealthService(DB_PATH, get_runtime_settings())


@lru_cache(maxsize=1)
def get_runtime_retention_service() -> RuntimeRetentionService:
    return RuntimeRetentionService(
        DB_PATH,
        upload_root=UPLOAD_DIR,
        output_root=OUTPUT_DIR,
        backup_roots=(DATA_DIR / "local-backups", DATA_DIR / "deploy-backups"),
        settings=get_runtime_settings(),
    )
