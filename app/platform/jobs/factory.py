from __future__ import annotations

from functools import lru_cache

from app.config import DB_PATH

from .repository import SQLiteJobRepository
from .service import JobService


@lru_cache(maxsize=1)
def get_job_repository() -> SQLiteJobRepository:
    return SQLiteJobRepository(DB_PATH)


@lru_cache(maxsize=1)
def get_job_service() -> JobService:
    return JobService(get_job_repository())
