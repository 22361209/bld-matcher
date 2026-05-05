from __future__ import annotations

from functools import wraps

from flask import flash, g, redirect, request, url_for
from werkzeug.security import check_password_hash


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
