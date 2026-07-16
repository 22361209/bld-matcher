from __future__ import annotations

from functools import lru_cache

from app.config import DB_PATH

from .repository import TubeUnitOfWork
from .service import TubeService


@lru_cache(maxsize=1)
def get_tube_service() -> TubeService:
    return TubeService(lambda: TubeUnitOfWork(DB_PATH))
