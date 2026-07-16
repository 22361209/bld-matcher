from __future__ import annotations


def register(app) -> None:
    from .web import register as register_web

    register_web(app)


__all__ = ["register"]
