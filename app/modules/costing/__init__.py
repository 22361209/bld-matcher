"""Product-cost calculation, storage and main application composition."""


def register(app) -> None:
    from app.modules.materials.factory import get_material_service
    from app.modules.tubes.factory import get_tube_service

    from .factory import get_costing_import_service, get_costing_service
    from .formatters import cost_time
    from .grid import format_cost_money
    from .historical_scrap_service import HistoricalScrapService
    from .import_web import costing_import
    from .material_price_web import material_prices
    from .web import costing
    from .web_support import template_context
    from .weight_lookup import CostWeightLookup

    app.extensions["costing_service"] = get_costing_service()
    app.extensions["historical_scrap_service"] = HistoricalScrapService(get_costing_service().db_path, app.secret_key)
    app.extensions["costing_weights"] = CostWeightLookup(app.secret_key, materials=get_material_service(),
                                                       tubes=get_tube_service())
    app.extensions["costing_imports"] = get_costing_import_service(str(app.secret_key))
    app.context_processor(template_context)
    app.jinja_env.filters["cost_money"] = format_cost_money
    app.jinja_env.filters["cost_time"] = cost_time
    app.register_blueprint(costing)
    app.register_blueprint(costing_import)
    app.register_blueprint(material_prices)
