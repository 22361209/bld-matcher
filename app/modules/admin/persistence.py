from __future__ import annotations

import sqlite3

from werkzeug.security import generate_password_hash

from app.matcher import compact_text
from app.platform.audit_store import log_event
from app.platform.clock import now_text


PASSWORD_HASH_METHOD = "pbkdf2:sha256"


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=PASSWORD_HASH_METHOD)


def list_audit_logs(
    connection: sqlite3.Connection,
    query: str = "",
    actor: str = "",
    limit: int = 300,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM audit_logs"
    params: list[object] = []
    clauses: list[str] = []
    if query.strip():
        key = f"%{query.strip()}%"
        clauses.append("(target_key LIKE ? OR detail LIKE ? OR action LIKE ? OR actor LIKE ?)")
        params.extend((key, key, key, key))
    if actor.strip():
        clauses.append("actor = ?")
        params.append(actor.strip())
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return connection.execute(sql, params).fetchall()


def list_log_actors(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row["actor"])
        for row in connection.execute(
            "SELECT DISTINCT actor FROM audit_logs WHERE actor IS NOT NULL AND actor != '' ORDER BY actor"
        )
    ]


def ensure_default_admin(
    connection: sqlite3.Connection,
    username: str | None = None,
    password: str | None = None,
) -> None:
    from app.config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_PASSWORD_PLACEHOLDER, DEFAULT_ADMIN_USERNAME

    username = username or DEFAULT_ADMIN_USERNAME
    password = password or DEFAULT_ADMIN_PASSWORD
    existing = connection.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return
    if not password or password == DEFAULT_ADMIN_PASSWORD_PLACEHOLDER:
        raise RuntimeError(
            "首次启动必须通过 .env 或环境变量显式设置 DEFAULT_ADMIN_PASSWORD，"
            "不能使用公开占位密码创建管理员。"
        )
    timestamp = now_text()
    connection.execute(
        """
        INSERT INTO users (username, display_name, password_hash, role, active, created_at, updated_at)
        VALUES (?, ?, ?, 'admin', 1, ?, ?)
        """,
        (username, "管理员", hash_password(password), timestamp, timestamp),
    )
    log_event(connection, "初始化管理员", "user", username, "创建默认管理员账号", actor="system")
    connection.commit()


def get_user_by_username(connection: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()


def get_user(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute("SELECT * FROM users ORDER BY active DESC, username").fetchall()


def save_user(
    connection: sqlite3.Connection,
    data: dict,
    actor: str = "",
    *,
    commit: bool = True,
) -> None:
    timestamp = now_text()
    user_id = data.get("id")
    username = compact_text(data.get("username"))
    if not username:
        raise ValueError("登录名不能为空。")
    role = data.get("role") or "viewer"
    if role not in {"admin", "editor", "user", "viewer"}:
        raise ValueError("角色无效。")
    active = 1 if str(data.get("active", "1")) != "0" else 0
    display_name = compact_text(data.get("display_name"))
    password = str(data.get("password") or "")
    if user_id:
        before = get_user(connection, int(user_id))
        if not before:
            raise ValueError("用户不存在。")
        params = {
            "id": int(user_id),
            "username": username,
            "display_name": display_name,
            "role": role,
            "active": active,
            "updated_at": timestamp,
        }
        password_sql = ""
        if password:
            params["password_hash"] = hash_password(password)
            password_sql = ", password_hash=:password_hash"
        connection.execute(
            f"""
            UPDATE users
            SET username=:username, display_name=:display_name, role=:role, active=:active,
                updated_at=:updated_at {password_sql}
            WHERE id=:id
            """,
            params,
        )
        changes = [
            f"{label}: {before[field]} -> {params[field]}"
            for field, label in {"username": "登录名", "display_name": "显示名", "role": "角色", "active": "状态"}.items()
            if str(before[field] or "") != str(params[field] or "")
        ]
        if password:
            changes.append("密码已重置")
        if changes:
            log_event(connection, "编辑账号", "user", username, "\n".join(changes), actor=actor)
    else:
        if not password:
            raise ValueError("新增用户必须设置密码。")
        connection.execute(
            """
            INSERT INTO users (username, display_name, password_hash, role, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (username, display_name, hash_password(password), role, active, timestamp, timestamp),
        )
        log_event(connection, "新增账号", "user", username, f"角色: {role}", actor=actor)
    if commit:
        connection.commit()
