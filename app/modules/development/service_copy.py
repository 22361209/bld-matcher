from __future__ import annotations

from typing import Any

from .domain import FIELDS, clean_fields, fail


class DevelopmentCopyMixin:
    def _mutation(self, project_id, *, expected_version, actor, action) -> Any:
        raise NotImplementedError

    def detail(self, project_id) -> dict:
        raise NotImplementedError

    def _resolve_borrowing(self, repo, values, *, current=None, actor) -> dict:
        raise NotImplementedError

    def copy_branch(self, project_id, source_branch_id, data, *, modes, copy_drawings=False, expected_version, actor):
        from .service import new_record, require_record

        values = clean_fields("branch", dict(data))
        # A copied product always awaits its own production readiness and owner confirmation.
        values["production_ready"] = 0
        values["actual_date"] = ""
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="复制另一手开发结构") as repo:
            original_branch = require_record(repo, "branches", source_branch_id, project_id)
            inherited = {key: original_branch[key] for key in FIELDS["branch"]}
            values = {**inherited, **values}
            if not copy_drawings:
                values["drawing_status"] = "not_started"
            aggregate = repo.export_aggregate(project_id)
            source = [row for row in aggregate["components"] if row["branch_id"] == source_branch_id and not row["archived"]]
            if not isinstance(modes, dict) or set(modes) != {row["id"] for row in source} or any(mode not in {"shared", "independent"} for mode in modes.values()):
                fail("请逐项明确选择共用或独立；不能根据编码自动判断。")
            component_codes = data.get("component_codes", {})
            source_by_id = {row["id"]: row for row in source}
            if not isinstance(component_codes, dict) or not set(component_codes) <= set(source_by_id):
                fail("目标编码核对项已变化，请重新打开复制核对表。")
            for identifier, overrides in component_codes.items():
                if not isinstance(overrides, dict) or not set(overrides) <= {"code", "borrowed_code"}:
                    fail("复制核对仅可调整本型号编码与借用编号。")
                component_codes[identifier] = clean_fields("component", overrides, current=source_by_id[identifier])
            branch = new_record({**values, "project_id": project_id}, actor=actor)
            repo.insert("branches", branch)
            mapping, pending = {}, list(source)
            task_map, operation_map = {}, {}
            while pending:
                ready = [row for row in pending if not row["parent_id"] or row["parent_id"] in mapping]
                if not ready:
                    fail("组件树存在无效父节点或循环。")
                for item in ready:
                    task_id = item["task_id"]
                    if modes[item["id"]] == "independent":
                        original = require_record(repo, "tasks", task_id, project_id)
                        copied = {key: value for key, value in original.items()
                                  if key not in {"id", "created_by", "created_at", "updated_at"}}
                        copied.update(status="not_started", actual_date="", archived=0)
                        if not copy_drawings:
                            copied["drawing_status"] = "not_started"
                        task = new_record(copied, actor=actor)
                        repo.insert("tasks", task)
                        for operation in aggregate["operations"]:
                            if operation["task_id"] == task_id and not operation["archived"]:
                                operation_values = {key: value for key, value in operation.items()
                                                    if key not in {"id", "created_by", "created_at", "updated_at"}}
                                operation_values.update(task_id=task["id"], status="not_started", actual_date="")
                                new_operation = new_record(operation_values, actor=actor)
                                repo.insert("operations", new_operation)
                                operation_map.setdefault(operation["id"], []).append(new_operation["id"])
                        task_map.setdefault(task_id, []).append(task["id"])
                        task_id = task["id"]
                    copied = {key: value for key, value in item.items()
                              if key not in {"id", "created_by", "created_at", "updated_at"}}
                    overrides = dict(component_codes.get(item["id"], {}))
                    for key in ("code", "borrowed_code"):
                        if key in overrides:
                            copied[key] = overrides[key]
                    copied.update(self._resolve_borrowing(repo, overrides, current=item, actor=actor))
                    copied.update(branch_id=branch["id"], parent_id=mapping.get(item["parent_id"]), task_id=task_id)
                    clean_fields("component", copied)
                    component = new_record(copied, actor=actor)
                    repo.insert("components", component)
                    mapping[item["id"]] = component["id"]
                    pending.remove(item)
            if copy_drawings:
                self._copy_task_document_links(repo, project_id, task_map, actor=actor)
                repo.copy_document_links(project_id, "branch", source_branch_id, branch["id"], actor=actor)
                for source_id, target_ids in operation_map.items():
                    for target_id in target_ids:
                        repo.copy_document_links(project_id, "operation", source_id, target_id, actor=actor)
        result = self.detail(project_id)
        result["copied_branch_id"] = branch["id"]
        return result

    @staticmethod
    def _copy_task_document_links(repo, project_id, task_map, *, actor):
        for source_id, target_ids in task_map.items():
            for target_id in target_ids:
                repo.copy_document_links(project_id, "task", source_id, target_id, actor=actor)
