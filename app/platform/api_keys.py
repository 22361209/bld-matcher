from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta

from app.platform.audit_store import log_event
from app.platform.clock import now_text
from app.matcher import compact_text

from .api_principal import ALL_API_SCOPES, DEFAULT_API_SCOPES, ApiPrincipal


def _hash_api_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _new_api_token() -> str:
    return f"bld_sk_{secrets.token_urlsafe(32)}"


def _api_key_preview(row: sqlite3.Row | None) -> str:
    if not row:
        return ""
    prefix = str(row["token_prefix"] or "bld_sk_")
    suffix = str(row["token_suffix"] or "")
    return f"{prefix}****{suffix}" if suffix else f"{prefix}****"


def normalize_scopes(scopes: Iterable[str] | None, *, default: frozenset[str] = DEFAULT_API_SCOPES) -> frozenset[str]:
    source = default if scopes is None else scopes
    selected = frozenset(str(scope).strip() for scope in source if str(scope).strip())
    unknown = selected - ALL_API_SCOPES
    if unknown:
        raise ValueError(f"未知 API Scope：{', '.join(sorted(unknown))}")
    if not selected:
        raise ValueError("API Key 至少需要一个 Scope。")
    return selected


def _parse_expiry(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        if len(text) == 10:
            return parsed.replace(hour=23, minute=59, second=59)
        return parsed
    except ValueError as exc:
        raise ValueError("API Key 到期时间格式无效。") from exc


def _scopes_from_row(row: sqlite3.Row) -> frozenset[str]:
    try:
        raw = json.loads(str(row["scopes"] or "[]"))
    except (json.JSONDecodeError, TypeError):
        raw = []
    return frozenset(scope for scope in raw if isinstance(scope, str) and scope in ALL_API_SCOPES)


def internal_api_key_status(conn: sqlite3.Connection) -> dict:
    timestamp = now_text()
    active = conn.execute(
        """
        SELECT * FROM internal_api_keys
        WHERE active = 1 AND (COALESCE(expires_at, '') = '' OR expires_at > ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (timestamp,),
    ).fetchone()
    latest = conn.execute(
        """
        SELECT * FROM internal_api_keys
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    row = active or latest
    return {
        "enabled": bool(active),
        "preview": _api_key_preview(active),
        "name": row["name"] if row else "OpenClaw",
        "created_by": row["created_by"] if row else "",
        "created_at": row["created_at"] if row else "",
        "updated_at": row["updated_at"] if row else "",
        "last_used_at": row["last_used_at"] if row else "",
        "expires_at": row["expires_at"] if row else "",
    }


def list_internal_api_keys(
    conn: sqlite3.Connection,
    *,
    rotation_days: int = 90,
    current_time: datetime | None = None,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, name, token_prefix, token_suffix, active, scopes, expires_at,
               created_by, created_at, updated_at, last_used_at
        FROM internal_api_keys
        ORDER BY active DESC, id DESC
        """
    ).fetchall()
    now = current_time or datetime.now()
    result = []
    for row in rows:
        expiry = _parse_expiry(row["expires_at"])
        expired = bool(row["active"] and expiry is not None and expiry <= now)
        created_at = _parse_expiry(row["created_at"])
        rotation_due_at = created_at + timedelta(days=rotation_days) if created_at else None
        rotation_due = bool(row["active"] and not expired and rotation_due_at and rotation_due_at <= now)
        result.append(
            {
                "id": row["id"],
                "name": row["name"],
                "preview": _api_key_preview(row),
                "active": bool(row["active"]),
                "usable": bool(row["active"] and not expired),
                "expired": expired,
                "rotation_due": rotation_due,
                "rotation_due_at": rotation_due_at.strftime("%Y-%m-%d") if rotation_due_at else "",
                "scopes": sorted(_scopes_from_row(row)),
                "expires_at": row["expires_at"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_used_at": row["last_used_at"],
            }
        )
    return result


def create_internal_api_key(
    conn: sqlite3.Connection,
    *,
    actor: str = "",
    name: str = "OpenClaw",
    scopes: Iterable[str] | None = None,
    expires_at: object = "",
    commit: bool = True,
) -> str:
    token = _new_api_token()
    timestamp = now_text()
    label = compact_text(name) or "OpenClaw"
    selected_scopes = normalize_scopes(scopes)
    expiry = _parse_expiry(expires_at)
    if expiry is not None and expiry <= datetime.now():
        raise ValueError("API Key 到期时间必须晚于当前时间。")
    expiry_text = expiry.strftime("%Y-%m-%d %H:%M:%S") if expiry else ""
    conn.execute(
        """
        INSERT INTO internal_api_keys
          (name, token_hash, token_prefix, token_suffix, active, scopes, expires_at,
           created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
        """,
        (
            label,
            _hash_api_token(token),
            "bld_sk_",
            token[-6:],
            json.dumps(sorted(selected_scopes)),
            expiry_text,
            actor,
            timestamp,
            timestamp,
        ),
    )
    log_event(
        conn,
        "Create internal API key",
        "internal_api_key",
        label,
        json.dumps({"scopes": sorted(selected_scopes), "expires_at": expiry_text}, ensure_ascii=False),
        actor=actor,
    )
    if commit:
        conn.commit()
    return token


def disable_internal_api_key(
    conn: sqlite3.Connection,
    *,
    actor: str = "",
    key_id: int | None = None,
    commit: bool = True,
) -> bool:
    timestamp = now_text()
    if key_id is None:
        cursor = conn.execute(
            "UPDATE internal_api_keys SET active = 0, updated_at = ? WHERE active = 1",
            (timestamp,),
        )
        target_key = "OpenClaw"
    else:
        row = conn.execute("SELECT name FROM internal_api_keys WHERE id = ?", (key_id,)).fetchone()
        cursor = conn.execute(
            "UPDATE internal_api_keys SET active = 0, updated_at = ? WHERE id = ? AND active = 1",
            (timestamp, key_id),
        )
        target_key = row["name"] if row else str(key_id)
    changed = cursor.rowcount > 0
    if changed:
        log_event(
            conn,
            "Disable internal API key",
            "internal_api_key",
            target_key,
            "Internal API key disabled.",
            actor=actor,
        )
        if commit:
            conn.commit()
    return changed


def verify_internal_api_token(conn: sqlite3.Connection, token: str) -> ApiPrincipal | None:
    token_hash = _hash_api_token(token)
    rows = conn.execute(
        "SELECT id, name, token_hash, scopes, expires_at, last_used_at FROM internal_api_keys WHERE active = 1"
    ).fetchall()
    now = datetime.now()
    for row in rows:
        if not hmac.compare_digest(str(row["token_hash"]), token_hash):
            continue
        expiry = _parse_expiry(row["expires_at"])
        if expiry is not None and expiry <= now:
            return None
        last_used = _parse_expiry(row["last_used_at"])
        if last_used is None or (now - last_used).total_seconds() >= 300:
            conn.execute(
                "UPDATE internal_api_keys SET last_used_at = ? WHERE id = ?",
                (now_text(), row["id"]),
            )
            conn.commit()
        return ApiPrincipal(
            key_id=int(row["id"]),
            integration_name=str(row["name"]),
            scopes=_scopes_from_row(row),
            expires_at=expiry,
        )
    return None
