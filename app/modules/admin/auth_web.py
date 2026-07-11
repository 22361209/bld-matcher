from __future__ import annotations

from flask import flash, redirect, render_template, request, session, url_for

from app.security import login_required, safe_redirect_target

from .factory import get_admin_service


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
