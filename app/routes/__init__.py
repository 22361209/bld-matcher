from __future__ import annotations


def register_routes(app) -> None:
    from . import admin, auth, customer_prices, home, inquiry, internal_api, materials, product_sync, products, purchase_contracts, shipment_notice, shipment_recognition

    auth.register(app)
    internal_api.register(app)
    home.register(app)
    materials.register(app)
    purchase_contracts.register(app)
    customer_prices.register(app)
    admin.register(app)
    product_sync.register(app)
    inquiry.register(app)
    products.register(app)
    shipment_notice.register(app)
    shipment_recognition.register(app)
