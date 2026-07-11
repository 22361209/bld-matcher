from __future__ import annotations

from app.modules.products.catalog_web import register as register_catalog
from app.modules.products.media_web import register as register_media
from app.modules.products.pricing_web import register as register_pricing
from app.modules.products.records_web import register as register_records


def register(app) -> None:
    register_catalog(app)
    register_media(app)
    register_pricing(app)
    register_records(app)
