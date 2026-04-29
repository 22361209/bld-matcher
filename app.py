from __future__ import annotations

from flask import Flask, flash, g, redirect, request, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from app.config import APP_DEBUG, APP_HOST, APP_PORT, DB_PATH, MAX_CONTENT_LENGTH, MAX_UPLOAD_MB, SECRET_KEY, assert_production_secrets
from app.database import connect, ensure_default_admin, get_user
from app.helpers import download_name, product_image_url
from app.routes import register_routes
from app.security import ROLE_LABELS, can


def create_app() -> Flask:
    assert_production_secrets()
    web_app = Flask(__name__)
    web_app.secret_key = SECRET_KEY
    web_app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    web_app.jinja_env.globals["can"] = can
    web_app.jinja_env.globals["ROLE_LABELS"] = ROLE_LABELS
    web_app.jinja_env.globals["product_image_url"] = product_image_url
    web_app.jinja_env.globals["download_name"] = download_name

    @web_app.before_request
    def load_current_user():
        with connect(DB_PATH) as conn:
            ensure_default_admin(conn)
            user_id = session.get("user_id")
            g.user = get_user(conn, int(user_id)) if user_id else None
        if request.endpoint in {"login", "do_login", "static"}:
            return
        if request.endpoint and not g.user:
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
        if g.user and not g.user["active"]:
            session.clear()
            flash("账号已停用。", "error")
            return redirect(url_for("login"))

    @web_app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        flash(f"上传文件不能超过 {MAX_UPLOAD_MB}MB。", "error")
        if request.path.startswith("/materials"):
            return redirect(url_for("materials"))
        if request.path.startswith("/prices"):
            return redirect(url_for("price_import"))
        if request.path.startswith("/catalog"):
            return redirect(url_for("products"))
        return redirect(url_for("index"))

    register_routes(web_app)
    return web_app


app = create_app()


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=APP_DEBUG)
