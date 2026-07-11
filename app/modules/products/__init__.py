from __future__ import annotations

from .api import register as register_api
from .sync_web import register as register_sync_web


def register(app) -> None:
    register_api(app)
    register_sync_web(app)


__all__ = ["register"]
