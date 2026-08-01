from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Callable, Protocol


class AdminUnitOfWork(Protocol):
    repository: object

    def __enter__(self): ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...


class UpdateReader(Protocol):
    @property
    def source_name(self) -> str: ...

    def read(self) -> list[dict[str, object]]: ...


@dataclass(frozen=True, slots=True)
class ApiKeyPage:
    status: dict[str, object]
    keys: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class AccessPage:
    users: list[dict[str, object]]
    roles: list[dict[str, object]]
    editing_user: dict[str, object] | None
    editing_role: dict[str, object] | None
    default_role_key: str
    can_create_user: bool


class AdminService:
    def __init__(
        self,
        unit_of_work_factory,
        update_reader: UpdateReader,
        password_verifier: Callable[[str, str], bool],
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.update_reader = update_reader
        self.password_verifier = password_verifier

    def user(self, user_id: int) -> dict[str, object] | None:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.user(user_id)

    def authenticate(self, username: str, password: str) -> dict[str, object] | None:
        with self.unit_of_work_factory() as unit_of_work:
            user = unit_of_work.repository.user_by_username(username.strip())
        if not user or not bool(user.get("active")):
            return None
        if not self.password_verifier(str(user.get("password_hash") or ""), password):
            return None
        return user

    def users(self, *, editing_id: int | None = None) -> tuple[list[dict[str, object]], dict[str, object] | None]:
        with self.unit_of_work_factory() as unit_of_work:
            rows = unit_of_work.repository.users()
            editing = unit_of_work.repository.user(editing_id) if editing_id is not None else None
        return rows, editing

    def access_page(
        self,
        *,
        editing_user_id: int | None = None,
        editing_role_key: str = "",
    ) -> AccessPage:
        with self.unit_of_work_factory() as unit_of_work:
            users = unit_of_work.repository.users()
            roles = unit_of_work.repository.roles()
            editing_user = (
                unit_of_work.repository.user(editing_user_id)
                if editing_user_id is not None
                else None
            )
            editing_role = (
                unit_of_work.repository.role(editing_role_key)
                if editing_role_key
                else None
            )
        non_system_role_keys = [
            str(role["role_key"])
            for role in roles
            if not bool(role["is_system"])
        ]
        default_role_key = (
            "viewer"
            if "viewer" in non_system_role_keys
            else next(iter(non_system_role_keys), "")
        )
        return AccessPage(
            users=users,
            roles=roles,
            editing_user=editing_user,
            editing_role=editing_role,
            default_role_key=default_role_key,
            can_create_user=bool(non_system_role_keys),
        )

    def save_user(
        self,
        data: Mapping[str, object],
        *,
        actor: str,
        actor_user_id: int | None = None,
    ) -> int:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.begin_write()
            user_id = unit_of_work.repository.save_user(
                data,
                actor=actor,
                actor_user_id=actor_user_id,
            )
            unit_of_work.commit()
        return user_id

    def save_role(
        self,
        data: Mapping[str, object],
        permissions: Iterable[str] | None,
        *,
        actor: str,
    ) -> str:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.begin_write()
            role_key = unit_of_work.repository.save_role(data, permissions, actor=actor)
            unit_of_work.commit()
        return role_key

    def delete_role(self, role_key: str, *, actor: str) -> None:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.begin_write()
            unit_of_work.repository.delete_role(role_key, actor=actor)
            unit_of_work.commit()

    def change_password(
        self,
        user_id: int,
        *,
        old_password: str,
        new_password: str,
        actor: str,
    ) -> None:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.begin_write()
            user = unit_of_work.repository.user(user_id)
            if not user:
                raise ValueError("账号不存在。")
            if not self.password_verifier(str(user.get("password_hash") or ""), old_password):
                raise ValueError("原密码不正确。")
            unit_of_work.repository.change_password(user_id, new_password, actor=actor)
            unit_of_work.commit()

    def update_user_overrides(self, user_id: int, overrides: object, *, actor: str) -> None:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.begin_write()
            unit_of_work.repository.update_user_overrides(user_id, overrides, actor=actor)
            unit_of_work.commit()

    def update_role_permissions(self, role_key: str, permissions: Iterable[str], *, actor: str) -> None:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.begin_write()
            unit_of_work.repository.update_role_permissions(role_key, permissions, actor=actor)
            unit_of_work.commit()

    def api_keys(self) -> ApiKeyPage:
        with self.unit_of_work_factory() as unit_of_work:
            status, keys = unit_of_work.repository.api_key_page()
        return ApiKeyPage(status=status, keys=keys)

    def create_api_key(
        self,
        *,
        actor: str,
        name: str,
        scopes: Iterable[str] | None,
        expires_at: object,
    ) -> tuple[str, ApiKeyPage]:
        with self.unit_of_work_factory() as unit_of_work:
            token = unit_of_work.repository.create_api_key(
                actor=actor,
                name=name,
                scopes=scopes,
                expires_at=expires_at,
            )
            status, keys = unit_of_work.repository.api_key_page()
            unit_of_work.commit()
        return token, ApiKeyPage(status=status, keys=keys)

    def delete_api_key(self, *, actor: str, key_id: int | None) -> bool:
        with self.unit_of_work_factory() as unit_of_work:
            changed = unit_of_work.repository.delete_api_key(actor=actor, key_id=key_id)
            unit_of_work.commit()
        return changed

    def logs(self, *, query: str = "", actor: str = "") -> tuple[list[dict[str, object]], list[str]]:
        with self.unit_of_work_factory() as unit_of_work:
            rows = unit_of_work.repository.logs(query=query, actor=actor)
            actors = unit_of_work.repository.log_actors()
        return rows, actors

    def system_updates(self) -> tuple[list[dict[str, object]], str]:
        return self.update_reader.read(), self.update_reader.source_name
