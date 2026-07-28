from __future__ import annotations


def register(app) -> None:
    from .api import register as register_api
    from .web import register as register_web

    register_web(app)
    register_api(app)


__all__ = ["register"]
