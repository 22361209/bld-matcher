"""Atomic tree-grid edits using the same aggregate boundary as individual editors."""
from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from app.platform.clock import now_text

from .domain import FIELDS, clean_bool, clean_fields, fail, validate_uuid


ROW_FIELDS = frozenset(FIELDS["component"]) | {"id", "client_id", "parent_client_id", "task"}
TASK_FIELDS = frozenset(FIELDS["task"]) | {"supplier_name", "create_supplier"}
MAX_GRID_ROWS = 500


def _row_error(message, index, *, field="", code="development.grid_invalid"):
    fail(f"第 {index + 1} 行：{message}", field=f"rows.{index}{'.' + field if field else ''}", code=code)


def _prepare_rows(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_GRID_ROWS:
        fail("请提交 1 至 500 行开发组件。", field="rows", code="development.grid_invalid")
    prepared, client_ids, existing_ids = [], {}, set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict) or set(raw) - ROW_FIELDS:
            _row_error("记录包含未知字段或格式无效。", index)
        row = dict(raw)
        identifier, client_id = row.get("id"), row.get("client_id")
        if bool(identifier) == bool(client_id):
            _row_error("已有组件须提供永久标识，新组件须提供唯一临时标识。", index)
        if identifier:
            validate_uuid(identifier)
            if identifier in existing_ids:
                _row_error("同一组件不能提交两次。", index)
            existing_ids.add(identifier)
        else:
            if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", client_id):
                _row_error("临时标识格式无效。", index)
            if client_id in client_ids:
                _row_error("临时标识不能重复。", index)
            client_ids[client_id] = str(uuid4())
        task = row.get("task", {})
        if not isinstance(task, dict) or set(task) - TASK_FIELDS:
            _row_error("任务内容包含未知字段或格式无效。", index, field="task")
        if row.get("parent_id") and row.get("parent_client_id"):
            _row_error("父节点不能同时指定已有标识和临时标识。", index, field="parent_id")
        prepared.append((index, row))
    for index, row in prepared:
        if row.get("parent_client_id"):
            parent_key = row["parent_client_id"]
            if not isinstance(parent_key, str) or parent_key not in client_ids:
                _row_error("临时父节点不在本次新增记录中。", index, field="parent_client_id")
            row["parent_id"] = client_ids[parent_key]
    return prepared, client_ids


def _resolve_supplier(repo, patch, *, actor, suppliers, created, index):
    from .service import new_record, require_record

    patch = dict(patch)
    create = patch.pop("create_supplier", False)
    name_supplied = "supplier_name" in patch
    name = patch.pop("supplier_name", "")
    if type(create) is not bool:
        _row_error("新建责任单位必须由用户明确确认。", index, field="task.create_supplier")
    if not isinstance(name, str):
        _row_error("责任单位名称格式无效。", index, field="task.supplier_name")
    name = name.strip()
    supplier_id = patch.get("supplier_id")
    if supplier_id:
        validate_uuid(supplier_id)
        supplier = require_record(repo, "suppliers", supplier_id)
        if create or (name and name != supplier["name"]):
            _row_error("责任单位名称与所选单位不一致，请重新选择。", index, field="task.supplier_id")
    elif name:
        if not create:
            _row_error("请选择已登记的责任单位，或明确确认新建。", index, field="task.supplier_name")
        if any(row["name"].casefold() == name.casefold() for row in suppliers):
            _row_error("已有同名责任单位，请从候选中明确选择。", index, field="task.supplier_name")
        key = name.casefold()
        if key not in created:
            supplier = new_record(clean_fields("supplier", {"name": name}), actor=actor)
            repo.insert("suppliers", supplier)
            created[key] = supplier["id"]
        patch["supplier_id"] = created[key]
    elif create:
        _row_error("请填写新责任单位名称。", index, field="task.supplier_name")
    elif name_supplied and "supplier_id" not in patch:
        patch["supplier_id"] = None
    return patch


def _merge_task_patch(target, patch, *, index):
    for key, value in patch.items():
        normalized = clean_bool(value) if key == "drawing_required" else str(value or "").strip()
        if key == "supplier_id":
            normalized = normalized or None
        if key in target and target[key] != normalized:
            _row_error("共用任务收到不同的字段值，请统一后再保存。", index,
                       field=f"task.{key}", code="development.shared_task_conflict")
        target[key] = normalized


class DevelopmentGridMixin:
    def _mutation(self, project_id, *, expected_version, actor, action) -> Any:
        raise NotImplementedError

    def _resolve_borrowing(self, repo, values, *, current=None, actor) -> dict:
        raise NotImplementedError

    def detail(self, project_id) -> dict:
        raise NotImplementedError

    def save_component_grid(self, project_id, branch_id, rows, *, expected_version, actor):
        """Patch supplied rows only. Missing rows and fields never mean deletion."""
        from .service import new_record, require_record

        prepared, client_ids = _prepare_rows(rows)
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="批量保存开发组件表") as repo:
            require_record(repo, "branches", branch_id, project_id)
            suppliers = repo.rows("suppliers")
            created_suppliers, task_patches, task_current = {}, {}, {}
            component_writes, affected_branches = [], set()
            for index, row in prepared:
                current = None
                if row.get("id"):
                    current = require_record(repo, "components", row["id"], project_id)
                    if current["branch_id"] != branch_id:
                        _row_error("组件属于其他成品。", index, field="id", code="development.not_found")
                component_values = clean_fields("component", row, current=current)
                component_values = self._resolve_borrowing(repo, component_values, current=current, actor=actor)
                if current:
                    component_id, task_id = current["id"], current["task_id"]
                    if task_id not in task_current:
                        task_current[task_id] = require_record(repo, "tasks", task_id, project_id)
                else:
                    component_id = client_ids[row["client_id"]]
                    task_id = str(uuid4())
                    task_current[task_id] = None
                patch = _resolve_supplier(repo, row.get("task", {}), actor=actor, suppliers=suppliers,
                                          created=created_suppliers, index=index)
                if not current:
                    patch.setdefault("name", component_values["name"])
                _merge_task_patch(task_patches.setdefault(task_id, {}), patch, index=index)
                component_writes.append((component_id, task_id, current, component_values))

            changed_tasks = set()
            for task_id, patch in task_patches.items():
                current = task_current[task_id]
                values = clean_fields("task", patch, current=current)
                if current is None:
                    repo.insert("tasks", new_record({**values, "id": task_id, "project_id": project_id}, actor=actor))
                    changed_tasks.add(task_id)
                else:
                    changed = {key: value for key, value in values.items() if current[key] != value}
                    if changed:
                        repo.update("tasks", task_id, {**changed, "updated_at": now_text()})
                        changed_tasks.add(task_id)
            for component_id, task_id, current, values in component_writes:
                if current is None:
                    repo.insert("components", new_record({**values, "id": component_id, "project_id": project_id,
                                                           "branch_id": branch_id, "task_id": task_id}, actor=actor))
                    affected_branches.add(branch_id)
                else:
                    changed = {key: value for key, value in values.items() if current[key] != value}
                    if changed:
                        repo.update("components", component_id, {**changed, "updated_at": now_text()})
                        affected_branches.add(branch_id)
            affected_branches.update(row["branch_id"] for row in repo.rows("components", project_id=project_id)
                                     if not row["archived"] and row["task_id"] in changed_tasks)
            repo.invalidate_confirmations(project_id, sorted(affected_branches))
        return {"detail": self.detail(project_id), "client_ids": client_ids}
