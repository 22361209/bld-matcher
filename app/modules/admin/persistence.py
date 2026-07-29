from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterable, Mapping

from werkzeug.security import generate_password_hash

from app.matcher import compact_text
from app.platform.audit_store import log_event
from app.platform.clock import now_text
from app.platform.permissions import (
    ADMIN_ONLY_PERMISSION_KEYS,
    ADMIN_ROLE_KEY,
    ALL_PERMISSION_KEYS,
    ASSIGNABLE_PERMISSION_KEYS,
    effective_permissions,
)


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


def _role_map(connection: sqlite3.Connection) -> dict[str, dict[str, object]]:
    roles = {
        str(row["role_key"]): dict(row)
        for row in connection.execute("SELECT * FROM roles ORDER BY is_system DESC, name COLLATE NOCASE").fetchall()
    }
    for role in roles.values():
        role["permissions"] = []
        role["user_count"] = 0
    for row in connection.execute(
        "SELECT role_key, permission FROM role_permissions ORDER BY role_key, permission"
    ).fetchall():
        role = roles.get(str(row["role_key"]))
        if role is not None:
            role["permissions"].append(str(row["permission"]))
    for row in connection.execute("SELECT role, COUNT(*) AS count FROM users GROUP BY role").fetchall():
        role = roles.get(str(row["role"]))
        if role is not None:
            role["user_count"] = int(row["count"])
    return roles


def _override_map(connection: sqlite3.Connection) -> dict[int, dict[str, str]]:
    overrides: dict[int, dict[str, str]] = {}
    for row in connection.execute(
        "SELECT user_id, permission, effect FROM user_permission_overrides ORDER BY user_id, permission"
    ).fetchall():
        overrides.setdefault(int(row["user_id"]), {})[str(row["permission"])] = str(row["effect"])
    return overrides


def _decorate_users(
    connection: sqlite3.Connection,
    rows: Iterable[sqlite3.Row],
) -> list[dict[str, object]]:
    roles = _role_map(connection)
    overrides = _override_map(connection)
    users: list[dict[str, object]] = []
    for row in rows:
        user = dict(row)
        role_key = str(user["role"])
        role = roles.get(role_key, {})
        stored_permissions = role.get("permissions", [])
        role_permissions = (
            {str(permission) for permission in stored_permissions}
            if isinstance(stored_permissions, (list, tuple, set, frozenset))
            else set()
        )
        user_overrides = overrides.get(int(user["id"]), {})
        user["role_name"] = str(role.get("name") or role_key)
        user["role_permissions"] = sorted(role_permissions)
        user["permission_overrides"] = dict(user_overrides)
        user["permissions"] = sorted(effective_permissions(role_key, role_permissions, user_overrides))
        users.append(user)
    return users


def get_user_by_username(connection: sqlite3.Connection, username: str) -> dict[str, object] | None:
    row = connection.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
    users = _decorate_users(connection, [row] if row is not None else [])
    return users[0] if users else None


def get_user(connection: sqlite3.Connection, user_id: int) -> dict[str, object] | None:
    row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    users = _decorate_users(connection, [row] if row is not None else [])
    return users[0] if users else None


def list_users(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute("SELECT * FROM users ORDER BY active DESC, username").fetchall()
    return _decorate_users(connection, rows)


def get_role(connection: sqlite3.Connection, role_key: str) -> dict[str, object] | None:
    return _role_map(connection).get(role_key)


def list_roles(connection: sqlite3.Connection) -> list[dict[str, object]]:
    return list(_role_map(connection).values())


def _permission_overrides(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("账号权限设置无效。")
    overrides = {str(permission): str(effect) for permission, effect in value.items() if str(effect) != "inherit"}
    unknown = set(overrides) - ALL_PERMISSION_KEYS
    invalid_effects = {effect for effect in overrides.values() if effect not in {"allow", "deny"}}
    if unknown or invalid_effects:
        raise ValueError("账号权限设置无效。")
    if set(overrides) & ADMIN_ONLY_PERMISSION_KEYS:
        raise ValueError("账号与角色管理权限只能由管理员角色持有。")
    return overrides


def save_user(
    connection: sqlite3.Connection,
    data: dict,
    actor: str = "",
    *,
    commit: bool = True,
    actor_user_id: int | None = None,
) -> int:
    timestamp = now_text()
    user_id = data.get("id")
    username = compact_text(data.get("username"))
    if not username:
        raise ValueError("登录名不能为空。")
    if len(username) > 80:
        raise ValueError("登录名不能超过 80 个字符。")
    role = str(data.get("role") or "viewer")
    if not get_role(connection, role):
        raise ValueError("角色无效。")
    active = 1 if str(data.get("active", "1")) != "0" else 0
    display_name = compact_text(data.get("display_name"))
    if len(display_name) > 80:
        raise ValueError("显示名不能超过 80 个字符。")
    password = str(data.get("password") or "")
    submitted_overrides = data.get("permission_overrides")
    normalized_overrides = _permission_overrides(submitted_overrides) if submitted_overrides is not None else None
    if role == ADMIN_ROLE_KEY:
        normalized_overrides = {}
    try:
        saved_user_id: int
        if user_id:
            saved_user_id = int(user_id)
            before = get_user(connection, saved_user_id)
            if not before:
                raise ValueError("用户不存在。")
            is_self = actor_user_id is not None and saved_user_id == actor_user_id
            if is_self and not active:
                raise ValueError("不能停用当前登录账号。")
            if is_self and before["role"] == ADMIN_ROLE_KEY and role != ADMIN_ROLE_KEY:
                raise ValueError("不能移除当前登录账号的管理员角色。")
            if bool(before["active"]) and before["role"] == ADMIN_ROLE_KEY and (
                not active or role != ADMIN_ROLE_KEY
            ):
                other_admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE id <> ? AND role = ? AND active = 1",
                    (saved_user_id, ADMIN_ROLE_KEY),
                ).fetchone()[0]
                if int(other_admins) == 0:
                    raise ValueError("系统至少需要保留一个启用的管理员账号。")
            params = {
                "id": saved_user_id,
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
                for field, label in {
                    "username": "登录名",
                    "display_name": "显示名",
                    "role": "角色",
                    "active": "状态",
                }.items()
                if str(before[field] or "") != str(params[field] or "")
            ]
            if password:
                changes.append("密码已重置")
            if normalized_overrides is not None and normalized_overrides != before["permission_overrides"]:
                changes.append(
                    "个人权限覆盖: "
                    + (", ".join(f"{key}={value}" for key, value in sorted(normalized_overrides.items())) or "无")
                )
            if changes:
                log_event(connection, "编辑账号", "user", username, "\n".join(changes), actor=actor)
        else:
            if not password:
                raise ValueError("新增用户必须设置密码。")
            cursor = connection.execute(
                """
                INSERT INTO users (username, display_name, password_hash, role, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (username, display_name, hash_password(password), role, active, timestamp, timestamp),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("新增账号未返回记录 ID。")
            saved_user_id = int(cursor.lastrowid)
            override_detail = ""
            if normalized_overrides:
                override_detail = "\n个人权限覆盖: " + ", ".join(
                    f"{key}={value}" for key, value in sorted(normalized_overrides.items())
                )
            log_event(
                connection,
                "新增账号",
                "user",
                username,
                f"角色: {role}{override_detail}",
                actor=actor,
            )
        if normalized_overrides is not None:
            connection.execute("DELETE FROM user_permission_overrides WHERE user_id = ?", (saved_user_id,))
            connection.executemany(
                """
                INSERT INTO user_permission_overrides (user_id, permission, effect, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (saved_user_id, permission, effect, timestamp)
                    for permission, effect in sorted(normalized_overrides.items())
                ),
            )
        if commit:
            connection.commit()
        return saved_user_id
    except sqlite3.IntegrityError as exc:
        if commit:
            connection.rollback()
        raise ValueError("登录名已存在。") from exc


def save_role(
    connection: sqlite3.Connection,
    data: Mapping[str, object],
    permissions: Iterable[str],
    *,
    actor: str,
    commit: bool = True,
) -> str:
    role_key = str(data.get("role_key") or "").strip()
    name = compact_text(data.get("name"))
    description = compact_text(data.get("description"))
    if not name:
        raise ValueError("角色名称不能为空。")
    if len(name) > 40:
        raise ValueError("角色名称不能超过 40 个字符。")
    if len(description) > 200:
        raise ValueError("角色说明不能超过 200 个字符。")
    permission_set = {str(permission) for permission in permissions}
    if permission_set - ALL_PERMISSION_KEYS:
        raise ValueError("角色包含未知权限。")
    if permission_set - ASSIGNABLE_PERMISSION_KEYS:
        raise ValueError("账号与角色管理权限只能由管理员角色持有。")
    timestamp = now_text()
    try:
        before = get_role(connection, role_key) if role_key else None
        if role_key:
            if not before:
                raise ValueError("角色不存在。")
            if bool(before["is_system"]):
                raise ValueError("管理员角色是系统固定角色，不能修改。")
            connection.execute(
                "UPDATE roles SET name = ?, description = ?, updated_at = ? WHERE role_key = ?",
                (name, description, timestamp, role_key),
            )
            action = "编辑角色"
        else:
            role_key = f"role_{uuid.uuid4().hex[:16]}"
            connection.execute(
                """
                INSERT INTO roles (role_key, name, description, is_system, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (role_key, name, description, timestamp, timestamp),
            )
            action = "新增角色"
        connection.execute("DELETE FROM role_permissions WHERE role_key = ?", (role_key,))
        connection.executemany(
            "INSERT INTO role_permissions (role_key, permission, created_at) VALUES (?, ?, ?)",
            ((role_key, permission, timestamp) for permission in sorted(permission_set)),
        )
        detail = f"权限: {', '.join(sorted(permission_set)) or '无'}"
        if before and str(before.get("name")) != name:
            detail = f"名称: {before.get('name')} -> {name}\n{detail}"
        log_event(connection, action, "role", role_key, detail, actor=actor)
        if commit:
            connection.commit()
        return role_key
    except sqlite3.IntegrityError as exc:
        if commit:
            connection.rollback()
        raise ValueError("角色名称已存在。") from exc


def delete_role(
    connection: sqlite3.Connection,
    role_key: str,
    *,
    actor: str,
    commit: bool = True,
) -> None:
    role = get_role(connection, role_key)
    if not role:
        raise ValueError("角色不存在。")
    if bool(role["is_system"]):
        raise ValueError("管理员角色是系统固定角色，不能删除。")
    if int(str(role["user_count"])):
        raise ValueError("该角色仍有账号在使用，请先调整这些账号的角色。")
    connection.execute("DELETE FROM role_permissions WHERE role_key = ?", (role_key,))
    connection.execute("DELETE FROM roles WHERE role_key = ?", (role_key,))
    log_event(connection, "删除角色", "role", role_key, f"角色名称: {role['name']}", actor=actor)
    if commit:
        connection.commit()
