from __future__ import annotations

import logging

from flask import flash, make_response, redirect, render_template, request, url_for

from app.platform.api_principal import API_SCOPE_LABELS, DEFAULT_API_SCOPES
from app.security import actor_name, permission_required

from .factory import get_admin_service


logger = logging.getLogger(__name__)


def _api_key_template_context(page, *, generated_token: str = "") -> dict[str, object]:
    return {
        "status": page.status,
        "keys": page.keys,
        "generated_token": generated_token,
        "scope_labels": API_SCOPE_LABELS,
        "default_scopes": DEFAULT_API_SCOPES,
    }


def register(app) -> None:
    @app.get("/users")
    @permission_required("manage_users")
    def users():
        rows, _editing = get_admin_service().users()
        return render_template("users.html", users=rows, editing=None)

    @app.get("/users/<int:user_id>/edit")
    @permission_required("manage_users")
    def edit_user(user_id: int):
        rows, editing = get_admin_service().users(editing_id=user_id)
        if not editing:
            flash("账号不存在。", "error")
            return redirect(url_for("users"))
        return render_template("users.html", users=rows, editing=editing)

    @app.post("/users/save")
    @permission_required("manage_users")
    def save_user_route():
        data = {
            "id": request.form.get("id", ""),
            "username": request.form.get("username", ""),
            "display_name": request.form.get("display_name", ""),
            "role": request.form.get("role", "viewer"),
            "active": request.form.get("active", "0"),
            "password": request.form.get("password", ""),
        }
        try:
            get_admin_service().save_user(data, actor=actor_name())
        except ValueError as exc:
            flash(f"账号保存失败：{exc}", "error")
            return redirect(url_for("users"))
        except Exception:
            logger.exception("User save failed")
            flash("账号保存失败，请稍后重试。", "error")
            return redirect(url_for("users"))
        flash("账号已保存。", "success")
        return redirect(url_for("users"))

    @app.get("/internal-api-key")
    @permission_required("manage_users")
    def internal_api_key():
        return render_template("internal_api_key.html", **_api_key_template_context(get_admin_service().api_keys()))

    @app.post("/internal-api-key/generate")
    @permission_required("manage_users")
    def generate_internal_api_key_route():
        name = request.form.get("name", "OpenClaw")
        scopes = request.form.getlist("scopes") if request.form.get("scope_selection_present") == "1" else None
        expires_at = request.form.get("expires_at", "").strip()
        try:
            token, page = get_admin_service().create_api_key(
                actor=actor_name(),
                name=name,
                scopes=scopes,
                expires_at=expires_at,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("internal_api_key"))
        flash("Internal API Key 已生成。请立即复制；离开本页后无法再次查看完整 Key。", "success")
        response = make_response(
            render_template(
                "internal_api_key.html",
                **_api_key_template_context(page, generated_token=token),
            )
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/internal-api-key/disable")
    @permission_required("manage_users")
    def disable_internal_api_key_route():
        key_id_text = request.form.get("key_id", "").strip()
        key_id = int(key_id_text) if key_id_text.isdigit() else None
        changed = get_admin_service().disable_api_key(actor=actor_name(), key_id=key_id)
        flash("Internal API Key 已停用。" if changed else "当前没有可停用的 Internal API Key。", "success")
        return redirect(url_for("internal_api_key"))

    @app.get("/logs")
    @permission_required("view_logs")
    def logs():
        query = request.args.get("q", "")
        actor = request.args.get("actor", "")
        rows, actors = get_admin_service().logs(query=query, actor=actor)
        return render_template("logs.html", logs=rows, query=query, actor=actor, actors=actors)

    @app.get("/system-updates")
    @permission_required("view_logs")
    def system_updates():
        updates, source_name = get_admin_service().system_updates()
        return render_template("system_updates.html", updates=updates, source_name=source_name)
