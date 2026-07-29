from __future__ import annotations

from functools import lru_cache

from app.config import BASE_DIR, DB_PATH, OUTPUT_DIR, PRODUCT_IMAGE_DATA_PREFIX, PRODUCT_IMAGE_DIR, SECRET_KEY
from app.modules.products.factory import get_product_service

from .infrastructure import (
    ContractCustomerDirectoryAdapter,
    ContractPdfAdapter,
    ContractProductImageResolver,
    QuoteSalesContractSourceAdapter,
    QuoteSelectionTokenAdapter,
)
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
        QuoteSalesContractSourceAdapter(),
        QuoteSelectionTokenAdapter(SECRET_KEY),
        customer_directory=ContractCustomerDirectoryAdapter(),
        document_root=OUTPUT_DIR,
    )
