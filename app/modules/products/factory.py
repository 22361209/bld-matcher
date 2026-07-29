from __future__ import annotations

from functools import lru_cache

from app.config import CATALOG_PATH, DB_PATH, MANUAL_MAP_PATH, PRODUCT_IMAGE_DIR, PRODUCT_IMAGE_THUMB_DIR
from app.matcher import load_manual_map

from .repository import SQLiteProductUnitOfWork
from .catalog_import import CatalogImportStorage
from .persistence import bootstrap_from_excel
from .service import ProductService


@lru_cache(maxsize=1)
def get_product_service() -> ProductService:
    return ProductService(
        lambda: SQLiteProductUnitOfWork(DB_PATH),
        lambda: bootstrap_from_excel(DB_PATH, CATALOG_PATH),
        lambda: load_manual_map(MANUAL_MAP_PATH),
        CatalogImportStorage(CATALOG_PATH, PRODUCT_IMAGE_DIR, PRODUCT_IMAGE_THUMB_DIR),
    )
