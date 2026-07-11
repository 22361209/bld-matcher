from __future__ import annotations

from .api import register as register_api
from .web import register as register_web


def register(app) -> None:
    register_web(app)
    register_api(app)
