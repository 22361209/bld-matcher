from __future__ import annotations

import hmac
from functools import wraps

from flask import g, jsonify, request

from app.config import DB_PATH, INTERNAL_API_TOKEN
from app.database import connect

from .api_errors import ApiError
from .api_keys import verify_internal_api_token
from .api_principal import ALL_API_SCOPES, ApiPrincipal


def _request_token() -> str:
    authorization = request.headers.get("Authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization.split(None, 1)[1].strip()
    return request.headers.get("X-Internal-API-Token", "").strip()


def authenticate_api_request() -> ApiPrincipal | None:
    token = _request_token()
    if not token:
        return None
    if INTERNAL_API_TOKEN and hmac.compare_digest(token, INTERNAL_API_TOKEN):
        return ApiPrincipal(
            key_id=None,
            integration_name="environment-fallback",
            scopes=ALL_API_SCOPES,
        )
    with connect(DB_PATH) as conn:
        return verify_internal_api_token(conn, token)


def current_api_principal() -> ApiPrincipal | None:
    principal = getattr(g, "api_principal", None)
    return principal if isinstance(principal, ApiPrincipal) else None


def api_actor_name() -> str:
    principal = current_api_principal()
    return principal.integration_name if principal else "internal-api"


def internal_api_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        principal = authenticate_api_request()
        if not principal:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "内部 API 未授权，请先在后台生成 API Key，并用 Authorization: Bearer <key> 调用。",
                    }
                ),
                401,
            )
        g.api_principal = principal
        return fn(*args, **kwargs)

    return wrapper


def api_scope_required(*required_scopes: str):
    required = frozenset(required_scopes)
    if not required:
        raise ValueError("At least one API scope is required.")
    unknown = required - ALL_API_SCOPES
    if unknown:
        raise ValueError(f"Unknown API scopes: {', '.join(sorted(unknown))}")

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            principal = authenticate_api_request()
            if not principal:
                raise ApiError("auth.unauthorized", "需要有效的 Bearer API Key。", 401)
            g.api_principal = principal
            if not principal.has_scopes(required):
                raise ApiError(
                    "auth.insufficient_scope",
                    "当前 API Key 没有执行此操作所需的权限。",
                    403,
                    {"required_scopes": sorted(required)},
                )
            return fn(*args, **kwargs)

        wrapper.__api_scopes__ = required
        return wrapper

    return decorator
