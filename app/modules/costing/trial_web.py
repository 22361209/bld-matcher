"""Independent trial authentication and thin adapters to shared cost views."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

from . import web_handlers as views
from .trial_security import login_required, permission_required
from .web_support import service


costing_trial = Blueprint("costing_trial", __name__)


@costing_trial.get("/health/ready")
def health_ready():
    service().list(page_size=1)
    return jsonify(ok=True, instance_id=current_app.config.get("COSTING_TRIAL_INSTANCE_ID", ""))


@costing_trial.route("/login", methods=["GET", "POST"])
@login_required(credentials=True)
def login():
    if request.method == "POST":
        return redirect(url_for("costing_trial.index"))
    return render_template("costing_login.html")


@costing_trial.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("costing_trial.login"))


@costing_trial.get("/")
@permission_required("view_costs")
def index():
    return views.index()


@costing_trial.get("/costs/new")
@permission_required("manage_costs")
def new():
    return views.new()


@costing_trial.get("/costs/<record_id>")
@permission_required("view_costs")
def edit(record_id: str):
    return views.edit(record_id)


@costing_trial.post("/costs/preview")
@permission_required("manage_costs")
def preview():
    return views.preview()


@costing_trial.post("/costs/new")
@permission_required("manage_costs")
def create():
    return views.create()


@costing_trial.post("/costs/<record_id>")
@permission_required("manage_costs")
def update(record_id: str):
    return views.update(record_id)
