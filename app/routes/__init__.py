from __future__ import annotations


def register_routes(app) -> None:
    from app.api.v1 import register as register_api_v1
    from app.modules.admin import register as register_admin
    from app.modules.business_sync import register as register_business_sync
    from app.modules.contracts import register as register_contracts
    from app.modules.customers import register as register_customers
    from app.modules.development.web import register as register_development
    from app.modules.development.web_structure import register as register_development_structure
    from app.modules.development.web_tasks import register as register_development_tasks
    from app.modules.development.web_documents import register as register_development_documents
    from app.modules.development.web_lookup import register as register_development_lookup
    from app.modules.development.web_grid import register as register_development_grid
    from app.modules.inquiry import register as register_inquiry_api
    from app.modules.materials import register as register_materials
    from app.modules.tubes import register as register_tubes
    from app.modules.products import register as register_products_api
    from app.modules.quotes import register as register_quotes
    from app.platform.runtime_web import register as register_runtime

    from . import home, inquiry, products

    register_runtime(app)
    register_inquiry_api(app)
    home.register(app)
    register_materials(app)
    register_tubes(app)
    register_development(app)
    register_development_structure(app)
    register_development_tasks(app)
    register_development_documents(app)
    register_development_lookup(app)
    register_development_grid(app)
    register_contracts(app)
    register_customers(app)
    register_quotes(app)
    register_admin(app)
    register_business_sync(app)
    inquiry.register(app)
    products.register(app)
    register_products_api(app)
    register_api_v1(app)
