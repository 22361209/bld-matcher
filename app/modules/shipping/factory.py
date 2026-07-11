from __future__ import annotations

from functools import lru_cache

from app.config import DATA_DIR, DB_PATH

from .infrastructure import ShippingTemplateStore, ShippingWorkbookAdapter
from .repository import SQLiteShippingUnitOfWork
from .service import ShippingNoticeService


@lru_cache(maxsize=1)
def get_shipping_notice_service() -> ShippingNoticeService:
    return ShippingNoticeService(
        lambda: SQLiteShippingUnitOfWork(DB_PATH),
        ShippingTemplateStore(DATA_DIR / "shipping_notice_templates"),
        ShippingWorkbookAdapter(),
    )
