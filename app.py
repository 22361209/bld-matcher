from __future__ import annotations

from pathlib import Path

from flask import Flask, Response, abort, flash, g, jsonify, redirect, request, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from app.config import APP_DEBUG, APP_HOST, APP_PORT, DB_PATH, MAX_CONTENT_LENGTH, MAX_UPLOAD_MB, PERMANENT_SESSION_LIFETIME, PRODUCT_IMAGE_MIGRATION_MARKER, PRODUCT_IMAGE_REQUEST_MAX_UPLOAD_MB, PRODUCT_SYNC_MAX_UPLOAD_MB, SECRET_KEY, assert_production_secrets
from app.database import connect
from app.helpers import download_name, product_image_thumb_url, product_image_url, product_image_urls, product_item_display_lines
from app.modules.admin.factory import get_admin_service
from app.modules.admin.persistence import ensure_default_admin
from app.modules.products.factory import get_product_service
from app.platform.api_errors import ApiError, error_response
from app.platform.logging_config import configure_logging
from app.platform.request_context import is_machine_api_path, register_request_context
from app.product_status import format_product_status
from app.routes import register_routes
from app.security import ROLE_LABELS, can, can_any, csrf_field, safe_referrer, validate_csrf_token, wants_json_response


# 同步类数据包（产品数据同步、业务数据同步）上传走更大的限额。
LARGE_UPLOAD_ENDPOINTS = {"preview_product_data_package", "preview_business_data_sync"}
PRODUCT_IMAGE_UPLOAD_ENDPOINTS = {"save_product", "upload_catalog"}


def _upload_limit_mb(endpoint: str | None) -> int:
    if endpoint in PRODUCT_IMAGE_UPLOAD_ENDPOINTS:
        return PRODUCT_IMAGE_REQUEST_MAX_UPLOAD_MB
    return PRODUCT_SYNC_MAX_UPLOAD_MB if endpoint in LARGE_UPLOAD_ENDPOINTS else MAX_UPLOAD_MB


def create_app() -> Flask:
    configure_logging()
    assert_production_secrets()
    web_app = Flask(__name__)
    web_app.secret_key = SECRET_KEY
    web_app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    web_app.config["PERMANENT_SESSION_LIFETIME"] = PERMANENT_SESSION_LIFETIME
    web_app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    web_app.config["SESSION_COOKIE_HTTPONLY"] = True
    web_app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    register_request_context(web_app)
    web_app.jinja_env.globals["can"] = can
    web_app.jinja_env.globals["can_any"] = can_any
    web_app.jinja_env.globals["ROLE_LABELS"] = ROLE_LABELS
    web_app.jinja_env.globals["product_image_url"] = product_image_url
    web_app.jinja_env.globals["product_image_thumb_url"] = product_image_thumb_url
    web_app.jinja_env.globals["product_image_urls"] = product_image_urls
    web_app.jinja_env.globals["product_item_display_lines"] = product_item_display_lines
    web_app.jinja_env.globals["format_product_status"] = format_product_status
    web_app.jinja_env.globals["download_name"] = download_name
    web_app.jinja_env.globals["csrf_field"] = csrf_field

    static_root = Path(web_app.static_folder or "static")

    def static_version(filename: str) -> int:
        try:
            return int((static_root / filename).stat().st_mtime)
        except OSError:
            return 0

    web_app.jinja_env.globals["static_version"] = static_version

    with connect(DB_PATH) as conn:
        ensure_default_admin(conn)

    @web_app.before_request
    def load_current_user():
        if PRODUCT_IMAGE_MIGRATION_MARKER.exists() and request.endpoint != "health_live":
            message = "产品图片正在升级，请稍后刷新页面。"
            if is_machine_api_path() or wants_json_response():
                return jsonify({"ok": False, "error": message}), 503
            return Response(message, status=503, mimetype="text/plain")
        if request.method == "POST" and request.content_length:
            limit_mb = _upload_limit_mb(request.endpoint)
            if request.content_length > limit_mb * 1024 * 1024:
                abort(413)
        if is_machine_api_path():
            g.user = None
            return
        if request.endpoint in {"login", "do_login", "health_live", "health_ready", "static"}:
            if request.method == "POST" and not web_app.config.get("TESTING") and not validate_csrf_token():
                if wants_json_response():
                    return jsonify({"ok": False, "error": "页面已过期，请刷新后重试。"}), 400
                flash("页面已过期，请刷新后重试。", "error")
                return redirect(safe_referrer(url_for("login")))
            return
        if request.method == "POST" and not web_app.config.get("TESTING") and not validate_csrf_token():
            if wants_json_response():
                return jsonify({"ok": False, "error": "页面已过期，请刷新后重试。"}), 400
            flash("页面已过期，请刷新后重试。", "error")
            return redirect(safe_referrer(url_for("index")))

        user_id = session.get("user_id")
        g.user = None
        if user_id:
            g.user = get_admin_service().user(int(user_id))
        if request.endpoint and not g.user:
            if wants_json_response():
                return jsonify({"ok": False, "error": "登录已失效，请刷新页面重新登录。"}), 401
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
        if g.user and not g.user["active"]:
            session.clear()
            if wants_json_response():
                return jsonify({"ok": False, "error": "账号已停用。"}), 403
            flash("账号已停用。", "error")
            return redirect(url_for("login"))

    @web_app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        limit_mb = _upload_limit_mb(request.endpoint)
        if request.path.startswith("/api/v1"):
            return error_response(
                ApiError("request.too_large", f"上传文件不能超过 {limit_mb}MB。", 413)
            )
        if is_machine_api_path() or wants_json_response():
            return jsonify({"ok": False, "error": f"上传文件不能超过 {limit_mb}MB。"}), 413
        flash(f"上传文件不能超过 {limit_mb}MB。", "error")
        if request.path.startswith("/product-data-sync"):
            return redirect(url_for("product_data_sync"))
        if request.path.startswith("/business-data-sync"):
            return redirect(url_for("business_data_sync"))
        if request.path.startswith("/materials"):
            return redirect(url_for("materials"))
        if request.path.startswith("/quotes"):
            return redirect(url_for("quote_web.quotes"))
        if request.path.startswith("/customer-prices"):
            return redirect(url_for("quote_web.quotes"))
        if request.path.startswith("/catalog"):
            return redirect(url_for("products"))
        return redirect(url_for("index"))

    register_routes(web_app)
    return web_app


app = create_app()


if __name__ == "__main__":
    get_product_service().warm_catalog()
    app.run(host=APP_HOST, port=APP_PORT, debug=APP_DEBUG)
