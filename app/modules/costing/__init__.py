"""Product-cost calculation, storage and main application composition."""


def register(app) -> None:
    from .factory import get_costing_import_service, get_costing_service
    from .formatters import cost_time
    from .grid import format_cost_money
    from .import_web import costing_import
    from .web import costing
    from .web_support import template_context

    app.extensions["costing_service"] = get_costing_service()
    app.extensions["costing_imports"] = get_costing_import_service(str(app.secret_key))
    app.context_processor(template_context)
    app.jinja_env.filters["cost_money"] = format_cost_money
    app.jinja_env.filters["cost_time"] = cost_time
    app.register_blueprint(costing)
    app.register_blueprint(costing_import)
