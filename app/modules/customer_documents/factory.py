from __future__ import annotations

from functools import lru_cache

from app.config import DATA_DIR, DB_PATH, MAX_UPLOAD_MB

from .infrastructure import CustomerDocumentFileStore
from .repository import SQLiteCustomerDocumentUnitOfWork
from .service import CustomerDocumentService


@lru_cache(maxsize=1)
def get_customer_document_service() -> CustomerDocumentService:
    return CustomerDocumentService(
        lambda: SQLiteCustomerDocumentUnitOfWork(DB_PATH),
        CustomerDocumentFileStore(
            DATA_DIR / "customer_files",
            max_file_bytes=MAX_UPLOAD_MB * 1024 * 1024,
        ),
    )
