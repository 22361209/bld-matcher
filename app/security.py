from __future__ import annotations

import hmac
import secrets
from functools import wraps
from urllib.parse import urlsplit

from flask import flash, g, redirect, request, session, url_for
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash


CSRF_SESSION_KEY = "_csrf_token"

ROLE_LABELS = {
    "admin": "管理员",
    "editor": "编辑员",
    "user": "普通用户",
    "viewer": "只读用户",
}

ROLE_PERMISSIONS = {
    "admin": {
        "manage_users",
        "import_catalog",
        "edit_products",
        "export_catalog",
        "manage_aliases",
        "generate_match",
        "view_logs",
        "generate_material_sheet",
        "generate_purchase_contract",
        "manage_materials",
        "view_customer_prices",
        "manage_customer_prices",
    },
    "editor": {"edit_products", "manage_aliases", "generate_match", "view_logs", "generate_material_sheet"},
    "user": {"generate_match", "generate_material_sheet"},
    "viewer": set(),
}


def actor_name() -> str:
    user = getattr(g, "user", None)
    if not user:
        return ""
    return user["username"]


def can(permission: str) -> bool:
    user = getattr(g, "user", None)
    if not user:
        return False
    return permission in ROLE_PERMISSIONS.get(user["role"], set())


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
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def permission_required(permission: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not getattr(g, "user", None):
                return redirect(url_for("login", next=request.path))
            if not can(permission):
                flash("当前账号没有权限执行这个操作。", "error")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator
