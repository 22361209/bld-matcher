from __future__ import annotations

from functools import lru_cache

from app.config import BASE_DIR, DB_PATH, OUTPUT_DIR, UPLOAD_DIR
from app.modules.products.factory import get_product_service
from app.platform.artifacts import SQLiteArtifactStore

from .infrastructure import WorkbookInquiryEngine
from .repository import SQLiteInquiryUnitOfWork
from .service import InquiryService


@lru_cache(maxsize=1)
def get_inquiry_service() -> InquiryService:
    return InquiryService(
        get_product_service(),
        WorkbookInquiryEngine(base_dir=BASE_DIR, upload_dir=UPLOAD_DIR, output_dir=OUTPUT_DIR),
        lambda: SQLiteInquiryUnitOfWork(DB_PATH),
        SQLiteArtifactStore(DB_PATH, (OUTPUT_DIR,)),
    )
