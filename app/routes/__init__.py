from __future__ import annotations


def register_routes(app) -> None:
    from app.api.v1 import register as register_api_v1
    from app.modules.admin import register as register_admin
    from app.modules.contracts import register as register_contracts
    from app.modules.inquiry import register as register_inquiry_api
    from app.modules.materials import register as register_materials
    from app.modules.products import register as register_products_api
    from app.modules.quotes import register as register_quotes
    from app.modules.shipping import register as register_shipping

    from . import auth, home, inquiry, product_sync, products, shipment_recognition

    auth.register(app)
    register_inquiry_api(app)
    home.register(app)
    register_materials(app)
    register_contracts(app)
    register_quotes(app)
    register_admin(app)
    product_sync.register(app)
    inquiry.register(app)
    products.register(app)
    register_shipping(app)
    shipment_recognition.register(app)
    register_products_api(app)
    register_api_v1(app)
