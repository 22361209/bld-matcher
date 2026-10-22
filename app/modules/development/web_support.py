"""Presentation helpers shared by the development HTML adapters."""
from __future__ import annotations

import logging
from math import ceil
from typing import Any

from flask import abort, flash, redirect, render_template, request, url_for

from app.security import can

from .factory import get_development_service


logger = logging.getLogger(__name__)
LABELS = {
    "not_started": "未开始", "in_progress": "进行中", "completed": "已完成",
    "partial_completed": "部分完成", "left": "左", "right": "右", "single": "单侧",
    "new": "新开发", "borrowed": "借用", "selected": "现成选型",
    "made": "自制", "purchased": "外购", "subcontracted": "委外",
    "ready": "已出图", "draft": "草稿", "released": "发布", "obsolete": "作废",
    "drawing": "图纸", "process": "工艺文件",
}
PROJECT_FIELDS = ("core_number", "title", "owner_username", "planned_date", "actual_date", "notes")
BRANCH_FIELDS = ("code", "name", "side", "owner_username", "planned_date", "actual_date", "notes", "drawing_required", "drawing_reason", "drawing_status")
COMPONENT_FIELDS = ("parent_id", "code", "borrowed_code", "borrowed_source_id", "name", "quantity", "position", "notes")
TASK_FIELDS = (
    "name", "status", "owner_username", "planned_date", "actual_date", "development_mode",
    "procurement_mode", "supplier_id", "material_category", "material_grade", "forming_process",
    "notes", "drawing_reason", "drawing_status",
)
OPERATION_FIELDS = ("task_id", "name", "sequence", "executor", "owner_username", "planned_date", "actual_date", "status", "notes")


def form_data(fields, *, prefix="") -> dict[str, Any]:
    return {key: request.form.get(prefix + key, "").strip() for key in fields}


def task_data(*, prefix=""):
    data = form_data(TASK_FIELDS, prefix=prefix)
    for key, default in (("status", "not_started"), ("development_mode", "new"), ("procurement_mode", "made"), ("drawing_status", "not_started")):
        if prefix + key not in request.form:
            data[key] = default
    data["drawing_required"] = request.form.get(prefix + "drawing_required", "1") == "1"
    return data


def version():
    raw = request.form.get("expected_version", "")
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError("missing version")
    return int(raw)


def detail_url(project_id, **kwargs):
    state: dict[str, Any] = {
        "view": request.form.get("view", request.args.get("view", "overview")),
        "branch": request.form.get("branch", request.args.get("branch", "")),
        "component": request.form.get("component", request.args.get("component", "")),
    }
    state.update(kwargs)
    return url_for("development_detail", project_id=project_id, **state)


def detail_context(project_id):
    try:
        context = get_development_service().detail(project_id)
    except ValueError as exc:
        if getattr(exc, "code", "") == "development.not_found":
            abort(404)
        raise
    if not context:
        abort(404)
    branches = context.get("branches", [])
    branch_id = request.values.get("branch", "")
    branch = next((item for item in branches if item["id"] == branch_id), branches[0] if branches else None)
    tasks = {item["id"]: item for item in context.get("tasks", [])}
    components = [dict(item) for item in context.get("components", [])]
    for item in components:
        item["task"] = tasks.get(item.get("task_id"), {})
        item["shared_count"] = sum(row.get("task_id") == item.get("task_id") for row in components)
    branch_components = [item for item in components if branch and item.get("branch_id") == branch["id"]]
    selected_id = request.values.get("component", "")
    selected = next((item for item in branch_components if item["id"] == selected_id), None)
    view = request.values.get("view", "overview")
    if view not in {"overview", "components", "documents"}:
        view = "overview"
    if view == "documents" and not can("view_development_files"):
        abort(403)
    context.update(
        selected_branch=branch, selected_component=selected,
        branch_components=branch_components, all_components=components,
        labels=LABELS, active_view=view, form_values={}, form_key="", errors=[], error_field="",
        can_edit=can("edit_development") and not context["project"].get("archived"),
    )
    context["suppliers"] = get_development_service().list_suppliers()
    context["borrowing_sources"] = get_development_service().list_sources()
    if can("view_development_files"):
        from .document_factory import get_development_document_service

        context["documents"] = get_development_document_service().list_documents(project_id)
    else:
        context["documents"] = []
    return context


def render_detail(project_id, *, errors=None, status=200, error_field=""):
    context = detail_context(project_id)
    if errors:
        context.update(errors=errors, form_values=request.form, form_key=request.form.get("form_key", ""), error_field=error_field)
    return render_template("development_detail.html", **context), status


def write_action(project_id, action, *, success="已保存。", destination=None):
    try:
        result = action()
    except ValueError as exc:
        message = getattr(exc, "message", "填写内容或页面版本无效，请检查后重试。")
        status = getattr(exc, "status_code", 400)
        if getattr(exc, "code", "").endswith("not_found"):
            status = 404
        if "conflict" in getattr(exc, "code", "") or "conflict" in type(exc).__name__.lower() or "版本" in message:
            status = 409
        return render_detail(project_id, errors=[message], status=status, error_field=getattr(exc, "field", ""))
    except Exception:
        logger.exception("Development write failed", extra={"project_id": project_id})
        return render_detail(project_id, errors=["保存失败，未确认的修改请稍后重试。"], status=500)
    flash(success, "success")
    target = destination(result) if callable(destination) else destination or detail_url(project_id)
    return redirect(str(target))


def paginate(rows, *, page_size=30):
    raw = request.args.get("page", "1")
    page = max(1, int(raw)) if raw.isdigit() else 1
    total = len(rows)
    total_pages = max(1, ceil(total / page_size))
    page = min(page, total_pages)
    query = request.args.to_dict()

    def page_url(number):
        return url_for("development_list", **{**query, "page": number})

    start = (page - 1) * page_size
    return rows[start:start + page_size], {
        "page": page, "total_pages": total_pages, "start": start + 1 if total else 0,
        "end": min(start + page_size, total), "has_prev": page > 1,
        "has_next": page < total_pages, "prev_url": page_url(page - 1),
        "next_url": page_url(page + 1), "jump_url": url_for("development_list"),
        "links": [{"page": n, "current": n == page, "url": page_url(n)} for n in range(max(1, page - 2), min(total_pages, page + 2) + 1)],
    }
