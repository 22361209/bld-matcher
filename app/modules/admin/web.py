from __future__ import annotations

import logging

from flask import flash, g, make_response, redirect, render_template, request, url_for

from app.matcher import matcher_rules, matcher_strategies
from app.platform.api_principal import API_SCOPE_LABELS, DEFAULT_API_SCOPES
from app.platform.permissions import PERMISSION_DEFINITIONS, permission_groups
from app.security import actor_name, permission_required

from .factory import get_admin_service


logger = logging.getLogger(__name__)


ACCESS_VIEWS = ("accounts", "account-list", "roles", "role-list")


def _access_page_context(
    *,
    view: str = "account-list",
    editing_user_id: int | None = None,
    editing_role_key: str = "",
    form_user: dict[str, object] | None = None,
    form_role: dict[str, object] | None = None,
) -> dict[str, object]:
    page = get_admin_service().access_page(
        editing_user_id=editing_user_id,
        editing_role_key=editing_role_key,
    )
    return {
        "view": view if view in ACCESS_VIEWS else "account-list",
        "users": page.users,
        "roles": page.roles,
        "editing_user": form_user if form_user is not None else page.editing_user,
        "editing_role": form_role if form_role is not None else page.editing_role,
        "default_role_key": page.default_role_key,
        "can_create_user": page.can_create_user,
        "permission_groups": permission_groups(),
        "permission_count": len(PERMISSION_DEFINITIONS),
    }


def _user_permission_overrides() -> dict[str, str]:
    if request.form.get("permission_selection_present") != "1":
        raise ValueError("权限表单不完整，请刷新后重试。")
    return {
        definition.key: request.form.get(f"permission_{definition.key}", "inherit")
        for definition in PERMISSION_DEFINITIONS
        if definition.assignable
    }


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
        view = request.args.get("view", "account-list")
        user_id_text = request.args.get("user_id", "").strip()
        return render_template(
            "users.html",
            **_access_page_context(
                view=view,
                editing_user_id=int(user_id_text) if view == "accounts" and user_id_text.isdigit() else None,
                editing_role_key=request.args.get("role_key", "") if view == "roles" else "",
            ),
        )

    @app.get("/users/<int:user_id>/edit")
    @permission_required("manage_users")
    def edit_user(user_id: int):
        context = _access_page_context(view="account-list", editing_user_id=user_id)
        if not context["editing_user"]:
            flash("账号不存在。", "error")
            return redirect(url_for("users"))
        return render_template("users.html", **context)

    @app.post("/users/<int:user_id>/permissions")
    @permission_required("manage_users")
    def save_user_permissions_route(user_id: int):
        try:
            get_admin_service().update_user_overrides(
                user_id,
                _user_permission_overrides(),
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"账号权限保存失败：{exc}", "error")
        except Exception:
            logger.exception("User permission save failed")
            flash("账号权限保存失败，请稍后重试。", "error")
        else:
            flash("账号权限已保存。", "success")
        return redirect(url_for("users", view="accounts", user_id=user_id))

    @app.post("/users/save")
    @permission_required("manage_users")
    def save_user_route():
        try:
            data = {
                "id": request.form.get("id", ""),
                "username": request.form.get("username", ""),
                "display_name": request.form.get("display_name", ""),
                "role": request.form.get("role", "viewer"),
                "active": request.form.get("active", "0"),
                "password": request.form.get("password", ""),
                "permission_overrides": (
                    _user_permission_overrides()
                    if request.form.get("permission_selection_present") == "1"
                    else None
                ),
            }
            get_admin_service().save_user(
                data,
                actor=actor_name(),
                actor_user_id=int(g.user["id"]),
            )
        except ValueError as exc:
            flash(f"账号保存失败：{exc}", "error")
            form_user = {
                "id": request.form.get("id", ""),
                "username": request.form.get("username", ""),
                "display_name": request.form.get("display_name", ""),
                "role": request.form.get("role", "viewer"),
                "active": request.form.get("active", "0") == "1",
                "permission_overrides": {},
            }
            return render_template(
                "users.html",
                **_access_page_context(view="account-list", form_user=form_user),
            ), 400
        except Exception:
            logger.exception("User save failed")
            flash("账号保存失败，请稍后重试。", "error")
            return redirect(url_for("users"))
        flash("账号已保存。", "success")
        return redirect(url_for("users"))

    @app.get("/roles/<role_key>/edit")
    @permission_required("manage_users")
    def edit_role(role_key: str):
        context = _access_page_context(view="role-list", editing_role_key=role_key)
        role = context["editing_role"]
        if not isinstance(role, dict):
            flash("角色不存在。", "error")
            return redirect(url_for("users", view="role-list"))
        if bool(role["is_system"]):
            flash("管理员角色是系统固定角色，不能修改。", "error")
            return redirect(url_for("users", view="role-list"))
        return render_template("users.html", **context)

    @app.post("/roles/save")
    @permission_required("manage_users")
    def save_role_route():
        data = {
            "role_key": request.form.get("role_key", ""),
            "name": request.form.get("name", ""),
            "description": "",
        }
        is_create = not data["role_key"]
        permissions = (
            request.form.getlist("permissions")
            if request.form.get("permission_selection_present") == "1"
            else None
        )
        try:
            saved_role_key = get_admin_service().save_role(data, permissions, actor=actor_name())
        except ValueError as exc:
            flash(f"角色保存失败：{exc}", "error")
            current = get_admin_service().access_page(
                editing_role_key=str(data["role_key"]),
            ).editing_role
            form_role = {
                **data,
                "permissions": [],
                "is_system": False,
                "user_count": int(str(current["user_count"])) if current else 0,
            }
            return render_template(
                "users.html",
                **_access_page_context(view="role-list", form_role=form_role),
            ), 400
        except Exception:
            logger.exception("Role save failed")
            flash("角色保存失败，请稍后重试。", "error")
            return redirect(url_for("users", view="role-list"))
        if is_create:
            flash("角色已创建，请为其勾选权限。", "success")
            return redirect(url_for("users", view="roles", role_key=saved_role_key))
        flash("角色已保存。", "success")
        return redirect(url_for("users", view="role-list"))

    @app.post("/roles/<role_key>/permissions")
    @permission_required("manage_users")
    def save_role_permissions_route(role_key: str):
        try:
            if request.form.get("permission_selection_present") != "1":
                raise ValueError("权限表单不完整，请刷新后重试。")
            get_admin_service().update_role_permissions(
                role_key,
                request.form.getlist("permissions"),
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"角色权限保存失败：{exc}", "error")
        except Exception:
            logger.exception("Role permission save failed")
            flash("角色权限保存失败，请稍后重试。", "error")
        else:
            flash("角色权限已保存，继承该角色的账号已即时生效。", "success")
        return redirect(url_for("users", view="roles", role_key=role_key))

    @app.post("/roles/<role_key>/delete")
    @permission_required("manage_users")
    def delete_role_route(role_key: str):
        try:
            get_admin_service().delete_role(role_key, actor=actor_name())
        except ValueError as exc:
            flash(f"角色删除失败：{exc}", "error")
        except Exception:
            logger.exception("Role delete failed")
            flash("角色删除失败，请稍后重试。", "error")
        else:
            flash("角色已删除。", "success")
        return redirect(url_for("users", view="role-list"))

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

    @app.post("/internal-api-key/delete")
    @permission_required("manage_users")
    def delete_internal_api_key_route():
        key_id_text = request.form.get("key_id", "").strip()
        key_id = int(key_id_text) if key_id_text.isdigit() else None
        changed = get_admin_service().delete_api_key(actor=actor_name(), key_id=key_id)
        flash("Internal API Key 已删除。" if changed else "当前没有可删除的 Internal API Key。", "success")
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

    @app.get("/matching-rules")
    @permission_required("manage_users")
    def matching_rules():
        return render_template(
            "matching_rules.html",
            active_page="matching_rules",
            brand_rules=matcher_rules(),
            strategies=matcher_strategies(),
        )
