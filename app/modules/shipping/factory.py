from __future__ import annotations

from functools import lru_cache

from app.config import DATA_DIR, DB_PATH, OUTPUT_DIR, UPLOAD_DIR
from app.platform.jobs.factory import get_job_service
from app.platform.runtime_factory import get_runtime_settings
from datetime import timedelta

from .infrastructure import ShippingTemplateStore, ShippingWorkbookAdapter
from .repository import SQLiteShippingUnitOfWork
from .recognition_service import ShipmentRecognitionService
from .service import ShippingNoticeService


@lru_cache(maxsize=1)
def get_shipping_notice_service() -> ShippingNoticeService:
    return ShippingNoticeService(
        lambda: SQLiteShippingUnitOfWork(DB_PATH),
        ShippingTemplateStore(DATA_DIR / "shipping_notice_templates"),
        ShippingWorkbookAdapter(),
    )


@lru_cache(maxsize=1)
def get_shipping_recognition_service() -> ShipmentRecognitionService:
    settings = get_runtime_settings()
    return ShipmentRecognitionService(
        get_job_service(),
        lambda: SQLiteShippingUnitOfWork(DB_PATH),
        upload_root=UPLOAD_DIR,
        output_root=OUTPUT_DIR,
        job_ttl=timedelta(days=settings.job_retention_days),
    )
