from functools import lru_cache

from app.config import DB_PATH

from .lookup_repository import open_lookup_repository
from .lookup_service import DevelopmentLookupService


@lru_cache(maxsize=1)
def get_development_lookup_service() -> DevelopmentLookupService:
    return DevelopmentLookupService(lambda: open_lookup_repository(DB_PATH))
