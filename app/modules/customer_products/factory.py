from __future__ import annotations

from functools import lru_cache

from app.config import DATA_DIR, DB_PATH, MAX_UPLOAD_MB

from .infrastructure import CustomerDrawingFileStore, ProductCatalogAdapter, QuoteHistoryAdapter
from .repository import SQLiteCustomerProductUnitOfWork
from .service import CustomerProductService


@lru_cache(maxsize=1)
def get_customer_product_service() -> CustomerProductService:
    return CustomerProductService(
        lambda: SQLiteCustomerProductUnitOfWork(DB_PATH),
        CustomerDrawingFileStore(
            DATA_DIR / "customer_files",
            max_file_bytes=MAX_UPLOAD_MB * 1024 * 1024,
        ),
        QuoteHistoryAdapter(),
        ProductCatalogAdapter(),
    )
