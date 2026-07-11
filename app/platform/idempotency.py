from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps

from flask import Response, current_app, make_response, request

from app.config import DB_PATH
from app.database import connect
from app.platform.clock import now_text

from .api_errors import ApiError
from .api_auth import current_api_principal
from .audit import current_audit_context, record_api_mutation
from .runtime_factory import get_runtime_settings


IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
IDEMPOTENCY_TTL = timedelta(hours=24)
IDEMPOTENCY_PENDING_TTL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    state: str
    status: int = 0
    body: str = ""
    content_type: str = "application/json"
    headers: tuple[tuple[str, str], ...] = ()


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _request_hash() -> str:
    digest = hashlib.sha256()
    digest.update(request.method.encode("ascii"))
    digest.update(b"\0")
    digest.update(request.path.encode("utf-8"))
    digest.update(b"?")
    digest.update(request.query_string)
    digest.update(b"\0")
    digest.update(request.get_data(cache=True))
    return digest.hexdigest()


def _claim(
    conn: sqlite3.Connection,
    *,
    principal_id: str,
    endpoint: str,
    method: str,
    key: str,
    request_hash: str,
) -> IdempotencyClaim:
    now = datetime.now()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM api_idempotency_keys WHERE expires_at <= ?", (_timestamp(now),))
    row = conn.execute(
        """
        SELECT request_hash, state, response_status, response_body, response_content_type, response_headers
        FROM api_idempotency_keys
        WHERE principal_id = ? AND method = ? AND endpoint = ? AND idempotency_key = ?
        """,
        (principal_id, method, endpoint, key),
    ).fetchone()
    if row:
        conn.commit()
        if not hmac.compare_digest(str(row["request_hash"]), request_hash):
            return IdempotencyClaim("conflict")
        if row["state"] == "completed":
            try:
                stored_headers = json.loads(str(row["response_headers"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                stored_headers = {}
            return IdempotencyClaim(
                "replay",
                int(row["response_status"]),
                str(row["response_body"]),
                str(row["response_content_type"] or "application/json"),
                tuple(
                    (str(name), str(value))
                    for name, value in stored_headers.items()
                    if name in {"ETag", "Location"}
                ),
            )
        return IdempotencyClaim("in_progress")
    timestamp = _timestamp(now)
    conn.execute(
        """
        INSERT INTO api_idempotency_keys
          (principal_id, method, endpoint, idempotency_key, request_hash, state,
           created_at, updated_at, expires_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            principal_id,
            method,
            endpoint,
            key,
            request_hash,
            timestamp,
            timestamp,
            _timestamp(now + IDEMPOTENCY_PENDING_TTL),
        ),
    )
    conn.commit()
    return IdempotencyClaim("claimed")


def _release(
    conn: sqlite3.Connection,
    *,
    principal_id: str,
    endpoint: str,
    method: str,
    key: str,
) -> None:
    conn.execute(
        """
        DELETE FROM api_idempotency_keys
        WHERE principal_id = ? AND method = ? AND endpoint = ? AND idempotency_key = ? AND state = 'pending'
        """,
        (principal_id, method, endpoint, key),
    )
    conn.commit()


def _complete(
    conn: sqlite3.Connection,
    *,
    principal_id: str,
    endpoint: str,
    method: str,
    key: str,
    response: Response,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        UPDATE api_idempotency_keys
        SET state = 'completed', response_status = ?, response_body = ?, response_content_type = ?,
            response_headers = ?, updated_at = ?, expires_at = ?
        WHERE principal_id = ? AND method = ? AND endpoint = ? AND idempotency_key = ? AND state = 'pending'
        """,
        (
            response.status_code,
            response.get_data(as_text=True),
            response.content_type,
            json.dumps(
                {
                    name: response.headers[name]
                    for name in ("ETag", "Location")
                    if name in response.headers
                },
                sort_keys=True,
            ),
            now_text(),
            _timestamp(datetime.now() + timedelta(hours=get_runtime_settings().idempotency_retention_hours)),
            principal_id,
            method,
            endpoint,
            key,
        ),
    )
    record_api_mutation(
        conn,
        context=current_audit_context(),
        endpoint=endpoint,
        method=method,
        status=response.status_code,
        idempotency_key=key,
    )
    conn.commit()


def idempotency_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        principal = current_api_principal()
        if principal is None:
            raise RuntimeError("idempotency_required must run after api_scope_required.")
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            raise ApiError("idempotency.required", "写操作必须提供 Idempotency-Key。", 400)
        if not IDEMPOTENCY_KEY_RE.fullmatch(key):
            raise ApiError(
                "idempotency.invalid",
                "Idempotency-Key 必须为 8 到 128 位字母、数字或 . _ : -。",
                400,
            )

        endpoint = request.endpoint or request.path
        method = request.method
        principal_id = principal.subject
        with connect(DB_PATH) as conn:
            claim = _claim(
                conn,
                principal_id=principal_id,
                endpoint=endpoint,
                method=method,
                key=key,
                request_hash=_request_hash(),
            )
        if claim.state == "conflict":
            raise ApiError(
                "idempotency.conflict",
                "该 Idempotency-Key 已用于不同的请求。",
                409,
            )
        if claim.state == "in_progress":
            raise ApiError(
                "idempotency.in_progress",
                "相同请求正在处理中，请稍后重试。",
                409,
                retryable=True,
            )
        if claim.state == "replay":
            response = current_app.response_class(
                claim.body,
                status=claim.status,
                content_type=claim.content_type,
            )
            response.headers["Idempotency-Replayed"] = "true"
            for name, value in claim.headers:
                response.headers[name] = value
            return response

        try:
            response = make_response(fn(*args, **kwargs))
        except Exception:
            with connect(DB_PATH) as conn:
                _release(conn, principal_id=principal_id, endpoint=endpoint, method=method, key=key)
            raise
        with connect(DB_PATH) as conn:
            if response.status_code >= 500:
                _release(conn, principal_id=principal_id, endpoint=endpoint, method=method, key=key)
            else:
                _complete(
                    conn,
                    principal_id=principal_id,
                    endpoint=endpoint,
                    method=method,
                    key=key,
                    response=response,
                )
        return response

    setattr(wrapper, "__idempotency_required__", True)
    return wrapper
