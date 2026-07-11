from __future__ import annotations

from functools import lru_cache

from app.config import BASE_DIR, DB_PATH
from app.platform.runtime_factory import get_runtime_settings
from app.security import password_matches

from .infrastructure import FileSystemUpdateReader
from .repository import SQLiteAdminUnitOfWork
from .service import AdminService


@lru_cache(maxsize=1)
def get_admin_service() -> AdminService:
    settings = get_runtime_settings()
    return AdminService(
        lambda: SQLiteAdminUnitOfWork(
            DB_PATH,
            api_key_rotation_days=settings.api_key_rotation_days,
        ),
        FileSystemUpdateReader(BASE_DIR),
        password_matches,
    )
