from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.config import DB_PATH
from app.database import connect, get_user, list_audit_logs, list_log_actors, list_users, save_user
from app.security import actor_name, permission_required


def register(app) -> None:
    @app.get("/users")
    @permission_required("manage_users")
    def users():
        with connect(DB_PATH) as conn:
            rows = list_users(conn)
        return render_template("users.html", users=rows, editing=None)

    @app.get("/users/<int:user_id>/edit")
    @permission_required("manage_users")
    def edit_user(user_id: int):
        with connect(DB_PATH) as conn:
            rows = list_users(conn)
            editing = get_user(conn, user_id)
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
            with connect(DB_PATH) as conn:
                save_user(conn, data, actor=actor_name())
        except Exception as exc:
            flash(f"账号保存失败：{exc}", "error")
            return redirect(url_for("users"))
        flash("账号已保存。", "success")
        return redirect(url_for("users"))

    @app.get("/logs")
    @permission_required("view_logs")
    def logs():
        query = request.args.get("q", "")
        actor = request.args.get("actor", "")
        with connect(DB_PATH) as conn:
            rows = list_audit_logs(conn, query=query, actor=actor)
            actors = list_log_actors(conn)
        return render_template("logs.html", logs=rows, query=query, actor=actor, actors=actors)
