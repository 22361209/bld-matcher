from __future__ import annotations

from .api import register as register_api


def register(app) -> None:
    register_api(app)


__all__ = ["register"]
