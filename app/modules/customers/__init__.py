from __future__ import annotations


def register(app) -> None:
    from .api import register as register_api
    from .documents_web import register as register_documents_web
    from .drawings_web import register as register_drawings_web
    from .products_web import register as register_products_web
    from .web import register as register_web

    register_web(app)
    register_documents_web(app)
    register_drawings_web(app)
    register_products_web(app)
    register_api(app)


__all__ = ["register"]
