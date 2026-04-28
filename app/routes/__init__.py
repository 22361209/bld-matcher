from __future__ import annotations


def register_routes(app) -> None:
    from . import admin, auth, home, inquiry, materials, products

    auth.register(app)
    home.register(app)
    materials.register(app)
    admin.register(app)
    inquiry.register(app)
    products.register(app)
