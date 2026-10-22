from __future__ import annotations

import logging

from flask import redirect, render_template, request, url_for

from app.security import actor_name, permission_required

from .factory import get_development_service
from .web_support import LABELS, PROJECT_FIELDS, form_data, paginate, render_detail


logger = logging.getLogger(__name__)


def register(app) -> None:
    @app.get("/development")
    @permission_required("view_development")
    def development_list():
        query = request.args.get("q", "").strip()[:200]
        status = request.args.get("status", "")
        archived = request.args.get("archived", "") == "1"
        rows = get_development_service().list_projects(query=query, status=status, include_archived=archived)
        page_rows, pagination = paginate(rows)
        return render_template("development_list.html", projects=page_rows, total=len(rows), pagination=pagination,
                               labels=LABELS, filters={"q": query, "status": status, "archived": archived})

    @app.get("/development/new")
    @permission_required("edit_development")
    def development_new():
        return render_template("development_form.html", form_values={}, form_key="project", errors=[], error_field="")

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
        return render_detail(project_id)
