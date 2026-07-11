from __future__ import annotations

from functools import lru_cache

from app.config import BASE_DIR, DB_PATH

from .infrastructure import FileSystemUpdateReader
from .repository import SQLiteAdminUnitOfWork
from .service import AdminService


@lru_cache(maxsize=1)
def get_admin_service() -> AdminService:
    return AdminService(
        lambda: SQLiteAdminUnitOfWork(DB_PATH),
        FileSystemUpdateReader(BASE_DIR),
    )
