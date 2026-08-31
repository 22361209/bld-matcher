from __future__ import annotations

import logging

from flask import current_app, flash, g, redirect, render_template, request, session, url_for

from app.security import actor_name, landing_page_options, login_required, permission_landing_url, safe_redirect_target

from .factory import get_admin_service


logger = logging.getLogger(__name__)
MOBILE_USER_AGENT_MARKERS = ("android", "iphone", "ipad", "ipod", "mobile")


def _login_client_mode() -> str:
    submitted = request.form.get("client_mode", "").strip().lower()
    if submitted in {"desktop", "mobile"}:
        return submitted
    user_agent = request.headers.get("User-Agent", "").lower()
    return "mobile" if any(marker in user_agent for marker in MOBILE_USER_AGENT_MARKERS) else "desktop"


def register(app) -> None:
    @app.get("/login")
    def login():
        return render_template("login.html", next=safe_redirect_target(request.args.get("next"), ""))

    @app.post("/login")
    def do_login():
        user = get_admin_service().authenticate(
            request.form.get("username", ""),
            request.form.get("password", ""),
        )
        if not user:
            flash("登录名或密码不正确。", "error")
            return redirect(url_for("login"))
        session.clear()
        session.permanent = True
        session["user_id"] = int(str(user["id"]))
        flash("登录成功。", "success")
        preferred_page = (
            user.get("default_mobile_page")
            if _login_client_mode() == "mobile"
            else user.get("default_page")
        )
        return redirect(
            safe_redirect_target(
                request.form.get("next"),
                permission_landing_url(user.get("permissions"), preferred_page),
            )
        )

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        flash("已退出登录。", "success")
        return redirect(url_for("login"))

    @app.get("/account/password")
    @login_required
    def change_password():
        return render_template(
            "account_password.html",
            landing_pages=landing_page_options(g.user.get("permissions")),
            session_retention_days=current_app.permanent_session_lifetime.days,
        )

    @app.post("/account/default-page")
    @login_required
    def save_default_page():
        default_page = request.form.get("default_page", "").strip()
        default_mobile_page = request.form.get("default_mobile_page", "").strip()
        allowed_pages = {option["key"] for option in landing_page_options(g.user.get("permissions"))}
        requested_pages = (default_page, default_mobile_page)
        if any(page and page not in allowed_pages for page in requested_pages):
            flash("默认页面保存失败：该页面不存在或当前账号没有访问权限。", "error")
            return redirect(url_for("change_password"))
        try:
            get_admin_service().update_default_pages(
                int(g.user["id"]),
                default_page,
                default_mobile_page,
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"默认页面保存失败：{exc}", "error")
        else:
            flash("登录默认页面已保存。", "success")
        return redirect(url_for("change_password"))

    @app.post("/account/password")
    @login_required
    def change_password_route():
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        try:
            if not new_password:
                raise ValueError("新密码不能为空。")
            if new_password != confirm_password:
                raise ValueError("两次输入的新密码不一致。")
            get_admin_service().change_password(
                int(g.user["id"]),
                old_password=request.form.get("old_password", ""),
                new_password=new_password,
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("account_password.html"), 400
        except Exception:
            logger.exception("Password change failed")
            flash("密码修改失败，请稍后重试。", "error")
            return render_template("account_password.html"), 500
        flash("密码已修改，下次登录请使用新密码。", "success")
        return redirect(url_for("change_password"))
