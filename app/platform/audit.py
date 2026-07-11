from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from flask import g, request

from app.platform.audit_store import log_event

from .api_principal import ApiPrincipal


@dataclass(frozen=True, slots=True)
class ApiAuditContext:
    principal: ApiPrincipal
    request_id: str
    remote_addr: str
    on_behalf_of: str


def current_audit_context() -> ApiAuditContext:
    principal = getattr(g, "api_principal", None)
    if not isinstance(principal, ApiPrincipal):
        raise RuntimeError("API audit context requires an authenticated principal.")
    payload = request.get_json(silent=True)
    on_behalf_of = ""
    if isinstance(payload, dict):
        on_behalf_of = str(payload.get("on_behalf_of") or "").strip()[:200]
    return ApiAuditContext(
        principal=principal,
        request_id=str(getattr(g, "request_id", "")),
        remote_addr=str(request.remote_addr or ""),
        on_behalf_of=on_behalf_of,
    )


def record_api_mutation(
    conn: sqlite3.Connection,
    *,
    context: ApiAuditContext,
    endpoint: str,
    method: str,
    status: int,
    idempotency_key: str,
    extra: dict[str, Any] | None = None,
) -> None:
    detail = {
        "request_id": context.request_id,
        "principal_id": context.principal.subject,
        "integration_name": context.principal.integration_name,
        "remote_addr": context.remote_addr,
        "on_behalf_of": context.on_behalf_of,
        "method": method,
        "status": status,
        "idempotency_key_suffix": idempotency_key[-8:],
        **(extra or {}),
    }
    log_event(
        conn,
        "API mutation",
        "api_endpoint",
        endpoint,
        json.dumps(detail, ensure_ascii=False, sort_keys=True),
        actor=context.principal.integration_name,
    )
