from __future__ import annotations

from app.drawings import product_drawing_path

from .domain import ProductRecord


def product_web_payload(record: ProductRecord) -> dict[str, object]:
    """Build a product payload with UI-only media availability state."""
    payload = record.web_payload()
    payload["drawing_available"] = product_drawing_path(payload) is not None
    return payload
