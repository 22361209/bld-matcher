from __future__ import annotations


def register_routes(app) -> None:
    from app.api.v1 import register as register_api_v1

    from . import admin, auth, home, inquiry, internal_api, material_drawings, materials, product_sync, products, purchase_contracts, quotes, shipment_notice, shipment_recognition

    auth.register(app)
    internal_api.register(app)
    home.register(app)
    materials.register(app)
    material_drawings.register(app)
    purchase_contracts.register(app)
    quotes.register(app)
    admin.register(app)
    product_sync.register(app)
    inquiry.register(app)
    products.register(app)
    shipment_notice.register(app)
    shipment_recognition.register(app)
    register_api_v1(app)
