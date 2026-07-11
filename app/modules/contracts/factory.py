from __future__ import annotations

from functools import lru_cache

from app.config import BASE_DIR, DB_PATH, PRODUCT_IMAGE_DATA_PREFIX, PRODUCT_IMAGE_DIR
from app.modules.products.factory import get_product_service

from .infrastructure import ContractPdfAdapter, ContractProductImageResolver
from .repository import SQLiteContractUnitOfWork
from .service import ContractService


@lru_cache(maxsize=1)
def get_contract_service() -> ContractService:
    return ContractService(
        lambda: SQLiteContractUnitOfWork(DB_PATH),
        get_product_service(),
        ContractPdfAdapter(),
        ContractProductImageResolver(
            base_dir=BASE_DIR,
            product_image_dir=PRODUCT_IMAGE_DIR,
            data_prefix=PRODUCT_IMAGE_DATA_PREFIX,
        ),
    )
