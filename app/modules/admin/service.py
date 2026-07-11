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

    def save_user(self, data: Mapping[str, object], *, actor: str) -> None:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.save_user(data, actor=actor)
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

    def disable_api_key(self, *, actor: str, key_id: int | None) -> bool:
        with self.unit_of_work_factory() as unit_of_work:
            changed = unit_of_work.repository.disable_api_key(actor=actor, key_id=key_id)
            unit_of_work.commit()
        return changed

    def logs(self, *, query: str = "", actor: str = "") -> tuple[list[dict[str, object]], list[str]]:
        with self.unit_of_work_factory() as unit_of_work:
            rows = unit_of_work.repository.logs(query=query, actor=actor)
            actors = unit_of_work.repository.log_actors()
        return rows, actors

    def system_updates(self) -> tuple[list[dict[str, object]], str]:
        return self.update_reader.read(), self.update_reader.source_name
