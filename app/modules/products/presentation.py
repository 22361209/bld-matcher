from __future__ import annotations

from app.drawings import product_drawing_path

from .domain import ProductRecord
from .catalog_brands import format_catalog_brands


def product_web_payload(
    record: ProductRecord,
    *,
    include_price: bool = True,
    include_drawing: bool = True,
    language: str = "en",
) -> dict[str, object]:
    """Build a product payload with UI-only media availability state."""
    payload = record.web_payload()
    payload["display_series"] = format_catalog_brands(record.series, language)
    payload["display_item"] = record.item_zh if language == "zh" else record.item
    payload["display_models"] = record.models_zh if language == "zh" else record.models
    if not include_price:
        payload.pop("price_cny", None)
    if include_drawing:
        payload["drawing_available"] = product_drawing_path(payload) is not None
    else:
        for key in ("drawing_path", "drawing_original_name", "drawing_available"):
            payload.pop(key, None)
    return payload
