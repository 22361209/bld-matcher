from functools import lru_cache

from app.config import DATA_DIR, DB_PATH, MAX_UPLOAD_MB

from .document_service import DevelopmentDocumentService
from .file_store import DevelopmentFileStore
from .repository import SQLiteDevelopmentUnitOfWork


@lru_cache(maxsize=1)
def get_development_document_service() -> DevelopmentDocumentService:
    return DevelopmentDocumentService(
        lambda: SQLiteDevelopmentUnitOfWork(DB_PATH),
        DevelopmentFileStore(DATA_DIR / "development_files", max_file_bytes=MAX_UPLOAD_MB * 1024 * 1024),
        legacy_roots={"product": DATA_DIR / "drawings", "material": DATA_DIR / "material_drawings"},
    )
