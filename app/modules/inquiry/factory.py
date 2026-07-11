from __future__ import annotations

from functools import lru_cache
from datetime import timedelta

from app.config import BASE_DIR, DB_PATH, OUTPUT_DIR, UPLOAD_DIR
from app.modules.products.factory import get_product_service
from app.platform.artifacts import SQLiteArtifactStore
from app.platform.runtime_factory import get_runtime_settings

from .infrastructure import WorkbookInquiryEngine
from .repository import SQLiteInquiryUnitOfWork
from .service import InquiryService


@lru_cache(maxsize=1)
def get_inquiry_service() -> InquiryService:
    settings = get_runtime_settings()
    return InquiryService(
        get_product_service(),
        WorkbookInquiryEngine(base_dir=BASE_DIR, upload_dir=UPLOAD_DIR, output_dir=OUTPUT_DIR),
        lambda: SQLiteInquiryUnitOfWork(DB_PATH),
        SQLiteArtifactStore(
            DB_PATH,
            (OUTPUT_DIR,),
            default_ttl=timedelta(hours=settings.artifact_retention_hours),
        ),
    )
