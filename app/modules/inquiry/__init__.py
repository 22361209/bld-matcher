from __future__ import annotations


def register(app) -> None:
    from .api import register as register_api

    register_api(app)


__all__ = ["register"]
