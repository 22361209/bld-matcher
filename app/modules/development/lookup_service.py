from __future__ import annotations

from .domain import fail, validate_uuid
from .lookup_ports import DevelopmentLookupRepository, LookupRepositoryFactory


SUGGESTED_TASK_FIELDS = ("name", "procurement_mode", "supplier_id", "material_category", "material_grade", "forming_process", "notes")


class DevelopmentLookupService:
    def __init__(self, repository_factory: LookupRepositoryFactory):
        self.repository_factory = repository_factory

    @staticmethod
    def _project(repository: DevelopmentLookupRepository, project_id: str) -> dict:
        validate_uuid(project_id)
        project = repository.project(project_id)
        if not project:
            fail("开发单不存在。", code="development.not_found")
        return project

    @staticmethod
    def _component(repository: DevelopmentLookupRepository, project_id: str, component_id: str) -> dict:
        validate_uuid(component_id)
        component = repository.component(project_id, component_id)
        if not component:
            fail("所选组件不存在或不属于当前开发单。", code="development.component_not_found")
        return component

    def search_sources(self, project_id: str, *, query: str = "", component_id: str = "", limit: int = 20) -> dict:
        query = str(query).strip()
        if len(query) > 100 or any(ord(char) < 32 for char in query):
            fail("检索内容最多 100 字，不能包含控制字符。", field="q")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            fail("检索数量须为 1 至 50。", field="limit")
        with self.repository_factory() as repository:
            project = self._project(repository, project_id)
            if component_id:
                self._component(repository, project_id, component_id)
            rows = repository.sources(query, limit=limit + 1)
            candidates = []
            for source in rows[:limit]:
                suggestions = repository.task_suggestions(source["id"], limit=6)
                candidates.append({**source, "task_suggestions": [self._suggestion(row) for row in suggestions[:5]],
                                   "suggestions_truncated": len(suggestions) > 5})
            return {"query": query, "project_version": project["version"], "candidates": candidates, "has_more": len(rows) > limit}

    @staticmethod
    def _suggestion(row: dict) -> dict:
        # The form may explicitly apply these suggestions. Work state and file adoption are never copied.
        return {key: row[key] for key in ("task_id", "project_id", "project_title", "component_id", "component_code", "supplier_name")} | {
            "fields": {**{key: row[key] for key in SUGGESTED_TASK_FIELDS}, "development_mode": "borrowed"},
        }

    def component_context(self, project_id: str, component_id: str) -> dict:
        with self.repository_factory() as repository:
            project = self._project(repository, project_id)
            component = self._component(repository, project_id, component_id)
            documents = repository.adopted_documents(project_id, component["task_id"])
            for document in documents:
                document["has_newer_revision"] = bool(document["has_newer_revision"])
                document["previewable"] = document.pop("content_type") == "application/pdf"
            return {"project_id": project_id, "project_version": project["version"], "component": component,
                    "target_type": "task", "target_id": component["task_id"], "documents": documents,
                    "upload_defaults": {"number": component["code"], "title": component["name"],
                                        "target": f"task|{component['task_id']}"}}
