"""An isolated Flask composition root for acceptance before project-page integration."""
from __future__ import annotations

import logging
import secrets
import fcntl
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, jsonify, request, session
from werkzeug.exceptions import HTTPException, SecurityError
from werkzeug.security import generate_password_hash

from app.security import can, can_any, csrf_field, csrf_token, validate_csrf_token

from .domain import CostingConflict, CostingNotFound, CostingValidationError
from .formatters import cost_time
from .grid import format_cost_money as cost_money
from .repository import initialize_costing_database
from .service import CostingService
from .trial_import_service import TrialImportService
from .trial_import_web import costing_import
from .trial_security import ACTOR, LoginAttempts, fail
from .trial_support import CONFIG_NAME, DATABASE_NAME, ROOT, atomic_json, load_settings
from .trial_web import costing_trial
from .transfer_web import register_transfer_routes
from .web_support import template_context


logger = logging.getLogger(__name__)


def initialize_trial(data_dir: Path, password: str | None = None) -> dict:
    directory = Path(data_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / ".initialize.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _initialize_locked(directory, password)


def _initialize_locked(directory: Path, password: str | None) -> dict:
    config_path = directory / CONFIG_NAME
    if config_path.exists():
        load_settings(directory)
        if not (directory / DATABASE_NAME).is_file():
            raise ValueError("访问配置已存在，但成本数据库缺失。请恢复已有成本库，不能自动重建空库。")
        initialize_costing_database(directory / DATABASE_NAME)
        return {"created": False, "password": None}
    if (directory / DATABASE_NAME).exists():
        raise ValueError("已有成本库但缺少访问配置，请恢复配置后继续，不能自动重置访问密码。")
    value = password if password is not None else secrets.token_urlsafe(18)
    if not isinstance(value, str) or len(value) < 12 or len(value) > 256:
        raise ValueError("试用访问密码应为 12 至 256 个字符。")
    try:
        initialize_costing_database(directory / DATABASE_NAME)
        atomic_json(config_path, {"format_version": 1, "password_hash": generate_password_hash(value),
                                 "secret_key": secrets.token_urlsafe(48), "credential_id": secrets.token_urlsafe(24)})
    except Exception:
        logger.exception("First cost trial initialization failed; removing new empty database")
        for suffix in ("", "-journal", "-wal", "-shm"):
            (directory / (DATABASE_NAME + suffix)).unlink(missing_ok=True)
        raise
    return {"created": True, "password": value}


def create_trial_app(data_dir: Path, read_only: bool = False) -> Flask:
    directory = Path(data_dir).expanduser().resolve()
    settings = load_settings(directory)
    if not (directory / DATABASE_NAME).is_file():
        raise ValueError("成本试用数据库不存在，请检查初始化状态。")
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.config.update(
        SECRET_KEY=settings["secret_key"], SESSION_COOKIE_NAME="bld_costing_trial", COSTING_TRIAL_MODE=True,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7), MAX_CONTENT_LENGTH=12 * 1024 * 1024,
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "::1"], COSTING_READ_ONLY=read_only,
        COSTING_PASSWORD_HASH=settings["password_hash"], COSTING_CREDENTIAL_ID=settings["credential_id"],
    )
    app.extensions["costing_service"] = CostingService(directory / DATABASE_NAME)
    app.extensions["costing_imports"] = TrialImportService(directory, settings["secret_key"], app.extensions["costing_service"])
    app.extensions["costing_login_attempts"] = LoginAttempts()
    app.jinja_env.globals.update(can=can, can_any=can_any, csrf_field=csrf_field, csrf_token=csrf_token,
                                static_version=lambda name: int((ROOT / "static" / name).stat().st_mtime))
    app.context_processor(template_context)
    app.jinja_env.filters["cost_money"] = cost_money
    app.jinja_env.filters["cost_time"] = cost_time

    @app.before_request
    def authenticate():
        g.user = None
        if session.get("costing_authenticated") == settings["credential_id"]:
            permissions = {"view_costs"} if read_only else {"view_costs", "manage_costs"}
            g.user = {"id": 1, "username": ACTOR, "role": "costing_trial", "permissions": permissions}
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not validate_csrf_token():
            return fail("页面已过期，请刷新后重试。", 400)

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.errorhandler(CostingValidationError)
    def invalid(error):
        if request.is_json:
            return jsonify(ok=False, error=error.message, field=error.field), 422
        return fail(error.message, 422)

    @app.errorhandler(CostingConflict)
    def conflict(error):
        return fail(error.message, 409)

    @app.errorhandler(CostingNotFound)
    def missing(error):
        return fail(error.message, 404)

    @app.errorhandler(HTTPException)
    def http_error(error):
        if isinstance(error, SecurityError):
            return jsonify(ok=False, error="仅允许通过本机地址访问成本试用环境。"), 400
        messages = {400: "请求格式不正确。", 413: "文件过大，请使用 12 MB 以内的工作簿。", 404: "页面不存在。"}
        return fail(messages.get(error.code, "请求无法完成。"), error.code or 500)

    @app.errorhandler(Exception)
    def unexpected(error):
        logger.exception("Cost trial request failed", extra={"endpoint": request.endpoint})
        return fail("暂时无法完成操作，请保留输入后重试。", 500)

    app.register_blueprint(costing_trial)
    app.register_blueprint(costing_import)
    register_transfer_routes(app, trial=True)
    return app
