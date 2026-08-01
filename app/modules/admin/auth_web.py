from __future__ import annotations

import logging

from flask import flash, g, redirect, render_template, request, session, url_for

from app.security import actor_name, login_required, safe_redirect_target

from .factory import get_admin_service


logger = logging.getLogger(__name__)


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
        session["user_id"] = int(str(user["id"]))
        flash("登录成功。", "success")
        return redirect(safe_redirect_target(request.form.get("next"), url_for("index")))

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        flash("已退出登录。", "success")
        return redirect(url_for("login"))

    @app.get("/account/password")
    @login_required
    def change_password():
        return render_template("account_password.html")

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
