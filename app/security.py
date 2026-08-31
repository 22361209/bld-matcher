from __future__ import annotations

import hmac
import secrets
from functools import wraps
from urllib.parse import urlsplit

from flask import current_app, flash, g, jsonify, redirect, request, session, url_for
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash

from app.platform.permissions import LEGACY_ROLE_LABELS, LEGACY_ROLE_PERMISSIONS


CSRF_SESSION_KEY = "_csrf_token"

ROLE_LABELS = LEGACY_ROLE_LABELS
ROLE_PERMISSIONS = LEGACY_ROLE_PERMISSIONS

LANDING_PAGE_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    ("index", "询价处理", "generate_match", "index"),
    ("customers", "客户信息", "view_customers", "customers"),
    ("quotes", "报价记录", "view_customer_prices", "quote_web.quotes"),
    ("contracts", "合同管理", "view_contracts", "contracts"),
    ("products", "产品目录", "view_products", "products"),
    ("tubes", "管件资料", "manage_tube_items", "tube_items"),
    ("materials", "生产料单", "generate_material_sheet", "materials"),
    ("material_drawings", "物料图纸", "view_material_drawings", "material_drawings"),
    ("users", "账号管理", "manage_users", "users"),
    ("logs", "操作日志", "view_logs", "logs"),
    ("business_data_sync", "业务数据同步", "sync_product_data", "business_data_sync"),
)
LANDING_PAGE_FALLBACK_ORDER = (
    "products",
    "index",
    "customers",
    "quotes",
    "contracts",
    "materials",
    "material_drawings",
    "tubes",
    "users",
    "logs",
    "business_data_sync",
)


def actor_name() -> str:
    user = getattr(g, "user", None)
    if not user:
        return ""
    return user["username"]


def can(permission: str) -> bool:
    user = getattr(g, "user", None)
    if not user:
        return False
    loaded_permissions = user.get("permissions") if hasattr(user, "get") else None
    if loaded_permissions is not None:
        return permission in loaded_permissions
    return permission in ROLE_PERMISSIONS.get(user["role"], set())


def can_any(*permissions: str) -> bool:
    return any(can(permission) for permission in permissions)


def wants_json_response() -> bool:
    return (
        request.headers.get("X-Requested-With") == "fetch"
        or request.accept_mimetypes.best == "application/json"
        or "application/json" in request.headers.get("Accept", "")
    )


def password_matches(stored_hash: str, password: str) -> bool:
    try:
        return check_password_hash(stored_hash, password)
    except AttributeError as exc:
        if str(stored_hash or "").startswith("scrypt:") and "scrypt" in str(exc):
            return False
        raise


def safe_redirect_target(target: str | None, default: str) -> str:
    target = (target or "").strip()
    if not target:
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return default
    return target


def landing_page_options(permissions: object | None = None) -> list[dict[str, str]]:
    granted = set(permissions) if permissions is not None else None

    def has(permission: str) -> bool:
        return permission in granted if granted is not None else can(permission)

    return [
        {"key": key, "label": label, "endpoint": endpoint}
        for key, label, permission, endpoint in LANDING_PAGE_DEFINITIONS
        if has(permission) and endpoint in current_app.view_functions
    ]


def permission_landing_url(
    permissions: object | None = None,
    preferred_page: object | None = None,
) -> str:
    options = landing_page_options(permissions)
    urls = {option["key"]: url_for(option["endpoint"]) for option in options}
    preferred_key = str(preferred_page or "").strip()
    if preferred_key in urls:
        return urls[preferred_key]
    for key in LANDING_PAGE_FALLBACK_ORDER:
        if key in urls:
            return urls[key]
    if "change_password" in current_app.view_functions:
        return url_for("change_password")
    if "index" in current_app.view_functions:
        return url_for("index")
    return "/"


def safe_referrer(default: str) -> str:
    referrer = (request.referrer or "").strip()
    if not referrer:
        return default
    parts = urlsplit(referrer)
    if parts.scheme or parts.netloc:
        if parts.netloc != request.host:
            return default
        path = parts.path or "/"
        if parts.query:
            path += f"?{parts.query}"
        if parts.fragment:
            path += f"#{parts.fragment}"
        return safe_redirect_target(path, default)
    return safe_redirect_target(referrer, default)


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_field() -> Markup:
    return Markup(f'<input type="hidden" name="csrf_token" value="{escape(csrf_token())}">')


def validate_csrf_token() -> bool:
    expected = session.get(CSRF_SESSION_KEY)
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    return bool(expected and submitted and hmac.compare_digest(str(expected), str(submitted)))


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not getattr(g, "user", None):
            if wants_json_response():
                return jsonify({"ok": False, "error": "登录已失效，请刷新页面重新登录。"}), 401
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def permission_required(permission: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not getattr(g, "user", None):
                if wants_json_response():
                    return jsonify({"ok": False, "error": "登录已失效，请刷新页面重新登录。"}), 401
                return redirect(url_for("login", next=request.path))
            if not can(permission):
                if wants_json_response():
                    return jsonify({"ok": False, "error": "当前账号没有权限执行这个操作。"}), 403
                flash("当前账号没有该页面权限，已返回产品目录。", "error")
                return redirect(permission_landing_url())
            return fn(*args, **kwargs)

        return wrapper

    return decorator
