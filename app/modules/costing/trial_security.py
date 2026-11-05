"""Authentication for the isolated acceptance server, never the production account database."""
from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from threading import Lock
from typing import Any, overload

from flask import current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app.security import can, wants_json_response

from .trial_support import new_owner_id


ACTOR = "成本试用管理员"


class LoginAttempts:
    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._lock = Lock()

    def check(self, address: str, password: str, password_hash: str) -> tuple[bool, bool]:
        with self._lock:
            now = time.monotonic()
            failures = [stamp for stamp in self._failures.get(address, []) if now - stamp < 300]
            if len(failures) >= 10:
                self._failures[address] = failures
                return False, True
            valid = len(password) <= 256 and check_password_hash(password_hash, password)
            self._failures[address] = [] if valid else [*failures, now]
            return valid, False


def fail(message: str, status: int):
    if wants_json_response() or request.is_json:
        return jsonify(ok=False, error=message), status
    if status == 401:
        return redirect(url_for("costing_trial.login"))
    flash(message, "error")
    if request.endpoint == "costing_trial.login":
        return render_template("costing_login.html", error=message), status
    return render_template("costing_list.html", result={"records": [], "total": 0, "page": 1,
                           "page_size": 50, "pages": 1}, filters={}), status


@overload
def login_required(fn: Callable[..., Any], *, credentials: bool = False) -> Callable[..., Any]: ...


@overload
def login_required(fn: None = None, *, credentials: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...


def login_required(fn=None, *, credentials: bool = False) -> Any:
    """Require an existing session or explicitly verify the submitted login credential."""
    def decorate(handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            if credentials:
                if request.method == "POST":
                    attempts = current_app.extensions["costing_login_attempts"]
                    valid, limited = attempts.check(
                        request.remote_addr or "local", request.form.get("password", ""),
                        current_app.config["COSTING_PASSWORD_HASH"],
                    )
                    if not valid:
                        return fail("尝试次数过多，请 5 分钟后重试。" if limited else "访问密码不正确。",
                                    429 if limited else 403)
                    session.clear()
                    session["costing_authenticated"] = current_app.config["COSTING_CREDENTIAL_ID"]
                    session["costing_owner"] = new_owner_id()
                    session.permanent = True
                return handler(*args, **kwargs)
            if not getattr(g, "user", None):
                return fail("请先进入成本试用环境。", 401)
            return handler(*args, **kwargs)
        return wrapped
    return decorate(fn) if fn is not None else decorate


def permission_required(permission: str):
    def decorate(handler):
        @wraps(handler)
        @login_required
        def wrapped(*args, **kwargs):
            if not can(permission):
                return fail("当前试用环境为只读，不能修改成本。", 403)
            return handler(*args, **kwargs)
        return wrapped
    return decorate


def actor() -> str:
    return ACTOR
