from __future__ import annotations

from .recognition_web import register as register_recognition
from .web import register as register_shipping_notices


def register(app) -> None:
    register_shipping_notices(app)
    register_recognition(app)


__all__ = ["register"]
