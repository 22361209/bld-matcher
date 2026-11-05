from __future__ import annotations

from flask import Blueprint

from . import web_handlers as views
from .trial_security import permission_required


costing_import = Blueprint("costing_import", __name__)


@costing_import.get("/import")
@permission_required("manage_costs")
def import_page():
    return views.import_page()


@costing_import.post("/import/preview")
@permission_required("manage_costs")
def preview_import():
    return views.preview_import()


@costing_import.post("/import/apply")
@permission_required("manage_costs")
def apply_import():
    return views.apply_import()
