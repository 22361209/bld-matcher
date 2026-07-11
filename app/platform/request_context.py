from __future__ import annotations

import re
import uuid

from flask import g, request


REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")


def is_machine_api_path(path: str | None = None) -> bool:
    value = path if path is not None else request.path
    return (
        value == "/api/internal"
        or value.startswith("/api/internal/")
        or value == "/api/quotes"
        or value.startswith("/api/quotes/")
        or value == "/api/v1"
        or value.startswith("/api/v1/")
    )


def _request_id_from_header() -> str:
    candidate = request.headers.get("X-Request-ID", "").strip()
    if REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def register_request_context(app) -> None:
    @app.before_request
    def establish_request_context() -> None:
        g.request_id = _request_id_from_header()

    @app.after_request
    def expose_request_context(response):
        response.headers["X-Request-ID"] = str(getattr(g, "request_id", ""))
        if is_machine_api_path():
            response.headers.setdefault("Cache-Control", "no-store")
        return response
