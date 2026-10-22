from functools import lru_cache

from app.config import DB_PATH

from .repository import SQLiteDevelopmentUnitOfWork
from .service import DevelopmentService
from .document_factory import get_development_document_service


@lru_cache(maxsize=1)
def get_development_service():
    return DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(DB_PATH),
                              drawing_verifier=get_development_document_service().has_valid_released_drawing)
