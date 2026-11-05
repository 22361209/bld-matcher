"""Main application routes use the existing account, permissions and CSRF boundary."""
from __future__ import annotations

from flask import Blueprint

from app.security import permission_required

from . import pair_views
from . import web_handlers as views
from .web_support import register_error_handlers


costing = Blueprint("costing", __name__, url_prefix="/costs")
register_error_handlers(costing)


@costing.get("")
@permission_required("view_costs")
def index():
    return views.index()


@costing.get("/new")
@permission_required("manage_costs")
def new():
    return views.new()


@costing.get("/<record_id>")
@permission_required("view_costs")
def edit(record_id: str):
    return views.edit(record_id)


@costing.post("/preview")
@permission_required("manage_costs")
def preview():
    return views.preview()


@costing.post("/new")
@permission_required("manage_costs")
def create():
    return views.create()


@costing.post("/<record_id>")
@permission_required("manage_costs")
def update(record_id: str):
    return views.update(record_id)


@costing.get("/pairs")
@permission_required("view_costs")
def pairs():
    return pair_views.candidates()


@costing.post("/pairs")
@permission_required("manage_costs")
def mirror_selected():
    return pair_views.mirror_selected()


@costing.get("/pairs/<record_id>")
@permission_required("view_costs")
def pair_review(record_id: str):
    return pair_views.review(record_id)


@costing.post("/pairs/<record_id>")
@permission_required("manage_costs")
def apply_pair(record_id: str):
    return pair_views.apply_review(record_id)
