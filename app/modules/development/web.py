from __future__ import annotations

import logging
from collections import Counter

from flask import abort, flash, redirect, render_template, request, url_for

from app.security import actor_name, can, permission_required

from .factory import get_development_service
from .web_support import LABELS, PROJECT_FIELDS, form_data, paginate, render_detail
from .worksheet import WORKSHEET_STATUSES, default_worksheet
from .worksheet_forms import render_sheet, save_sheet


logger = logging.getLogger(__name__)


def register(app) -> None:
    @app.get("/development")
    @permission_required("view_development")
    def development_list():
        query = request.args.get("q", "").strip()[:200]
        legacy = request.args.get("type") == "legacy"
        archived = legacy and request.args.get("archived") == "1"
        legacy_status = request.args.get("status", "") if legacy else ""
        rows = get_development_service().list_projects(query=query, status=legacy_status, include_archived=archived,
                                                      worksheet=not legacy)
        selected, status_options = [], []
        if not legacy:
            requested = set(request.args.getlist("status"))
            selected = [value for value in WORKSHEET_STATUSES if value in requested]
            counts = Counter(row["status"] for row in rows)
            status_options = [{"value": value, "label": label, "count": counts[value]}
                              for value, label in WORKSHEET_STATUSES.items()]
            if selected:
                rows = [row for row in rows if row["status"] in selected]
        page_rows, pagination = paginate(rows)
        return render_template("development_list.html", projects=page_rows, total=len(rows), pagination=pagination,
                               labels=LABELS if legacy else WORKSHEET_STATUSES, legacy=legacy,
                               filters={"q": query, "status": legacy_status, "archived": archived},
                               selected_statuses=selected, status_options=status_options)

    @app.get("/development/new")
    @permission_required("edit_development")
    def development_new():
        return render_sheet(default_worksheet())

    @app.post("/development/worksheets")
    @permission_required("edit_development")
    def development_create_sheet():
        return save_sheet()

    @app.post("/development/<project_id>/worksheet")
    @permission_required("edit_development")
    def development_update_sheet(project_id):
        return save_sheet(project_id)

    @app.post("/development/create")
    @permission_required("edit_development")
    def development_create():
        try:
            context = get_development_service().create_project(form_data(PROJECT_FIELDS), actor=actor_name())
        except ValueError as exc:
            return render_template("development_form.html", form_values=request.form, form_key="project",
                                   errors=[getattr(exc, "message", "请检查系列号、名称及日期。")], error_field=getattr(exc, "field", "")), 400
        except Exception:
            logger.exception("Development project creation failed")
            return render_template("development_form.html", form_values=request.form, form_key="project",
                                   errors=["创建失败，填写内容已保留，请稍后重试。"], error_field=""), 500
        return redirect(url_for("development_detail", project_id=context["project"]["id"]))

    @app.get("/development/<project_id>")
    @permission_required("view_development")
    def development_detail(project_id):
        try:
            context = get_development_service().detail(project_id)
        except ValueError as exc:
            if getattr(exc, "code", "") == "development.not_found":
                abort(404)
            raise
        if "worksheet" in context:
            project = context["project"]
            return render_sheet(context["worksheet"], project_id=project_id, expected_version=project["version"],
                                editable=can("edit_development") and not project["archived"], notes=project["notes"])
        return render_detail(project_id, context=context)

    @app.post("/development/<project_id>/delete-legacy")
    @permission_required("edit_development")
    def development_delete_legacy(project_id):
        from .document_factory import get_development_document_service

        try:
            title = get_development_document_service().delete_legacy_project(
                project_id, expected_version=request.form.get("expected_version"),
                confirmed_title=request.form.get("confirmed_title", ""), actor=actor_name(),
            )
        except ValueError as exc:
            if getattr(exc, "code", "") == "development.not_found":
                abort(404)
            if getattr(exc, "code", "") == "development.worksheet_type":
                abort(400)
            status = 409 if getattr(exc, "code", "") == "development.version_conflict" else 400
            return render_detail(project_id, errors=[getattr(exc, "message", "请核对开发单后重试。")], status=status)
        flash(f"旧开发单「{title}」已删除。", "success")
        return redirect(url_for("development_list", type="legacy"))
