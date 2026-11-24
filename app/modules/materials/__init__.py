from __future__ import annotations


def register(app) -> None:
    from .web import register as register_web

    from .bom_web import register as register_bom

    register_web(app)
    register_bom(app)


__all__ = ["register"]
