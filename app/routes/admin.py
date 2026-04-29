from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.config import BASE_DIR, DB_PATH
from app.database import connect, get_user, list_audit_logs, list_log_actors, list_users, save_user
from app.security import actor_name, permission_required


UPDATES_DOC_PATH = BASE_DIR / "项目交接说明.md"


def parse_update_heading(heading: str) -> dict[str, str]:
    parts = [part.strip() for part in heading.split("·")]
    if len(parts) >= 3:
        return {"date": parts[0], "version": parts[1], "title": " · ".join(parts[2:])}
    if len(parts) == 2:
        return {"date": parts[0], "version": parts[1], "title": "重要变更"}
    return {"date": heading.strip(), "version": "", "title": "重要变更"}


def read_system_updates() -> list[dict[str, object]]:
    if not UPDATES_DOC_PATH.exists():
        return []

    updates: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    in_section = False
    for raw_line in UPDATES_DOC_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == "## 当前最近重要变更":
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("## "):
            break
        if line.startswith("### "):
            current = {**parse_update_heading(line.removeprefix("### ").strip()), "entries": []}
            updates.append(current)
            continue
        if line.startswith("- ") and current is not None:
            current["entries"].append(line.removeprefix("- ").strip())
    return [item for item in updates if item["entries"]]


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

    @app.get("/system-updates")
    @permission_required("view_logs")
    def system_updates():
        return render_template(
            "system_updates.html",
            updates=read_system_updates(),
            source_name=UPDATES_DOC_PATH.name,
        )
