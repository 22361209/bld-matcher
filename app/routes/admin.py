from __future__ import annotations

import json

from flask import flash, make_response, redirect, render_template, request, url_for

from app.config import BASE_DIR, DB_PATH
from app.database import (
    connect,
    get_user,
    list_audit_logs,
    list_log_actors,
    list_users,
    save_user,
)
from app.platform.api_keys import (
    create_internal_api_key,
    disable_internal_api_key,
    internal_api_key_status,
    list_internal_api_keys,
)
from app.platform.api_principal import API_SCOPE_LABELS, DEFAULT_API_SCOPES
from app.security import actor_name, permission_required


UPDATES_DOC_PATH = BASE_DIR / "项目交接说明.md"
CHANGE_FRAGMENTS_DIR = BASE_DIR / "changes"


def parse_update_heading(heading: str) -> dict[str, str]:
    parts = [part.strip() for part in heading.split("·")]
    if len(parts) >= 3:
        return {"date": parts[0], "version": parts[1], "title": " · ".join(parts[2:])}
    if len(parts) == 2:
        return {"date": parts[0], "version": parts[1], "title": "重要变更"}
    return {"date": heading.strip(), "version": "", "title": "重要变更"}


def read_archived_system_updates() -> list[dict[str, object]]:
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


def read_change_fragments() -> list[dict[str, object]]:
    updates = []
    if not CHANGE_FRAGMENTS_DIR.is_dir():
        return updates
    for path in sorted(CHANGE_FRAGMENTS_DIR.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries = payload.get("entries")
        if not isinstance(entries, list) or not entries:
            continue
        updates.append(
            {
                "date": str(payload.get("date") or ""),
                "version": str(payload.get("version") or ""),
                "title": str(payload.get("title") or "重要变更"),
                "entries": [str(entry) for entry in entries],
            }
        )
    return updates


def read_system_updates() -> list[dict[str, object]]:
    updates = [*read_change_fragments(), *read_archived_system_updates()]
    unique = []
    seen = set()
    for item in updates:
        key = (item["date"], item["title"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


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

    @app.get("/internal-api-key")
    @permission_required("manage_users")
    def internal_api_key():
        with connect(DB_PATH) as conn:
            status = internal_api_key_status(conn)
            keys = list_internal_api_keys(conn)
        return render_template(
            "internal_api_key.html",
            status=status,
            keys=keys,
            generated_token="",
            scope_labels=API_SCOPE_LABELS,
            default_scopes=DEFAULT_API_SCOPES,
        )

    @app.post("/internal-api-key/generate")
    @permission_required("manage_users")
    def generate_internal_api_key_route():
        name = request.form.get("name", "OpenClaw")
        scopes = (
            request.form.getlist("scopes")
            if request.form.get("scope_selection_present") == "1"
            else None
        )
        expires_at = request.form.get("expires_at", "").strip()
        try:
            with connect(DB_PATH) as conn:
                token = create_internal_api_key(
                    conn,
                    actor=actor_name(),
                    name=name,
                    scopes=scopes,
                    expires_at=expires_at,
                )
                status = internal_api_key_status(conn)
                keys = list_internal_api_keys(conn)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("internal_api_key"))
        flash("Internal API Key 已生成。请立即复制；离开本页后无法再次查看完整 Key。", "success")
        response = make_response(
            render_template(
                "internal_api_key.html",
                status=status,
                keys=keys,
                generated_token=token,
                scope_labels=API_SCOPE_LABELS,
                default_scopes=DEFAULT_API_SCOPES,
            )
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/internal-api-key/disable")
    @permission_required("manage_users")
    def disable_internal_api_key_route():
        key_id_text = request.form.get("key_id", "").strip()
        key_id = int(key_id_text) if key_id_text.isdigit() else None
        with connect(DB_PATH) as conn:
            changed = disable_internal_api_key(conn, actor=actor_name(), key_id=key_id)
        flash("Internal API Key 已停用。" if changed else "当前没有可停用的 Internal API Key。", "success")
        return redirect(url_for("internal_api_key"))


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
            source_name=f"changes/*.json + {UPDATES_DOC_PATH.name}",
        )
