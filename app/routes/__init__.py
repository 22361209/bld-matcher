from __future__ import annotations


def register_routes(app) -> None:
    from . import admin, auth, customer_prices, home, inquiry, materials, products, purchase_contracts

    auth.register(app)
    home.register(app)
    materials.register(app)
    purchase_contracts.register(app)
    customer_prices.register(app)
    admin.register(app)
    inquiry.register(app)
    products.register(app)
