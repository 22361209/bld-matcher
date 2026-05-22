from __future__ import annotations

from flask import flash, redirect, render_template, request, session, url_for

from app.config import DB_PATH
from app.database import connect, get_user_by_username
from app.security import login_required, password_matches, safe_redirect_target


def register(app) -> None:
    @app.get("/login")
    def login():
        return render_template("login.html", next=safe_redirect_target(request.args.get("next"), ""))

    @app.post("/login")
    def do_login():
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with connect(DB_PATH) as conn:
            user = get_user_by_username(conn, username)
        if not user or not user["active"] or not password_matches(user["password_hash"], password):
            flash("登录名或密码不正确。", "error")
            return redirect(url_for("login"))
        session["user_id"] = user["id"]
        flash("登录成功。", "success")
        return redirect(safe_redirect_target(request.form.get("next"), url_for("index")))

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        flash("已退出登录。", "success")
        return redirect(url_for("login"))
