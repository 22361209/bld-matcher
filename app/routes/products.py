from __future__ import annotations

from app.modules.products.catalog_web import register as register_catalog
from app.modules.products.media_web import register as register_media
from app.modules.products.options_web import register as register_options
from app.modules.products.records_web import register as register_records


def register(app) -> None:
    for register_module in (register_catalog, register_media, register_options, register_records):
        register_module(app)
