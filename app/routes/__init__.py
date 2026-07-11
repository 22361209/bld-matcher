from __future__ import annotations


def register_routes(app) -> None:
    from app.api.v1 import register as register_api_v1
    from app.modules.inquiry import register as register_inquiry_api
    from app.modules.products import register as register_products_api
    from app.modules.quotes import register as register_quotes

    from . import admin, auth, home, inquiry, material_drawings, materials, product_sync, products, purchase_contracts, shipment_notice, shipment_recognition

    auth.register(app)
    register_inquiry_api(app)
    home.register(app)
    materials.register(app)
    material_drawings.register(app)
    purchase_contracts.register(app)
    register_quotes(app)
    admin.register(app)
    product_sync.register(app)
    inquiry.register(app)
    products.register(app)
    shipment_notice.register(app)
    shipment_recognition.register(app)
    register_products_api(app)
    register_api_v1(app)
