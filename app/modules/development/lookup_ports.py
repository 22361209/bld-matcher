from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol


class DevelopmentLookupRepository(Protocol):
    def project(self, project_id: str) -> dict | None: ...

    def component(self, project_id: str, component_id: str) -> dict | None: ...

    def sources(self, query: str, *, limit: int) -> list[dict]: ...

    def task_suggestions(self, source_id: str, *, limit: int) -> list[dict]: ...

    def adopted_documents(self, project_id: str, task_id: str) -> list[dict]: ...


LookupRepositoryFactory = Callable[[], AbstractContextManager[DevelopmentLookupRepository]]
