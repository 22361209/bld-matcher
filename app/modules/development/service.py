from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from uuid import uuid4

from app.platform.clock import now_text

from .domain import audit_detail, clean_fields, fail, validate_aggregate
from .ports import UnitOfWorkFactory
from .service_copy import DevelopmentCopyMixin
from .service_grid import DevelopmentGridMixin


def new_record(data, *, actor):
    timestamp = now_text()
    return {"id": str(uuid4()), **data, "created_by": actor, "created_at": timestamp, "updated_at": timestamp}


def require_record(repository, kind, identifier, project_id=None):
    item = repository.get(kind, identifier)
    if not item or (project_id and item.get("project_id") != project_id):
        fail("记录不存在或不属于当前开发单。", code="development.not_found")
    if item.get("archived"):
        fail("记录已归档。", code="development.archived")
    return item


class DevelopmentService(DevelopmentCopyMixin, DevelopmentGridMixin):
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, *, drawing_verifier=None):
        self.unit_of_work_factory = unit_of_work_factory
        self.drawing_verifier = drawing_verifier

    def _has_drawing(self, project_id, target_type, target_id):
        # A metadata row alone cannot establish production readiness. Production composition
        # injects a verifier which checks each selected immutable file's size and SHA-256.
        return bool(self.drawing_verifier and self.drawing_verifier(project_id, target_type, target_id))

    @contextmanager
    def _mutation(self, project_id, *, expected_version, actor, action):
        if not str(actor or "").strip():
            fail("写入需要已认证的操作人。")
        with self.unit_of_work_factory() as unit:
            repo = unit.repository
            project_before = repo.claim_project(project_id, expected_version)
            before = repo.export_aggregate(project_id)
            before["project"] = project_before
            yield repo
            after = repo.export_aggregate(project_id)
            validate_aggregate(after)
            repo.audit(action, project_id, audit_detail(before, after), actor=actor)
            unit.commit()

    def create_project(self, data, *, actor):
        if not str(actor or "").strip():
            fail("写入需要已认证的操作人。")
        values = clean_fields("project", dict(data))
        project = new_record(values, actor=actor)
        with self.unit_of_work_factory() as unit:
            unit.repository.insert("projects", project)
            unit.repository.audit("新增开发单", project["id"], audit_detail(None, project), actor=actor)
            unit.commit()
        return self.detail(project["id"])

    def detail(self, project_id):
        with self.unit_of_work_factory() as unit:
            data = unit.repository.export_aggregate(project_id)
            return self._decorate(data, unit.repository)

    def _decorate(self, data, repo):
        tasks = {row["id"]: row for row in data["tasks"] if not row["archived"]}
        active_components = [row for row in data["components"] if not row["archived"]]
        task_ids = {row["task_id"] for row in active_components}
        missing_drawings = {identifier for identifier in task_ids if tasks[identifier]["drawing_required"]
                            and not self._has_drawing(data["project"]["id"], "task", identifier)}
        today = date.today().isoformat()
        branches = [row for row in data["branches"] if not row["archived"]]
        missing_drawings.update(row["id"] for row in branches if row["drawing_required"]
                                and not self._has_drawing(data["project"]["id"], "branch", row["id"]))
        for branch in branches:
            owned = [tasks[row["task_id"]] for row in active_components if row["branch_id"] == branch["id"]]
            finished = owned and all(task["status"] == "completed" for task in owned)
            ready = (branch["id"] not in missing_drawings and not any(row["id"] in missing_drawings for row in owned)
                     and not any(row["procurement_mode"] != "made" and not row["supplier_id"] for row in owned)
                     and not any(row["task_id"] in {task["id"] for task in owned} and not row["archived"]
                                 and row["status"] != "completed" for row in data["operations"]))
            branch["status"] = ("completed" if finished and ready and branch["production_ready"] and branch["confirmed_by"]
                                else "in_progress" if any(task["status"] != "not_started" for task in owned)
                                else "not_started")
        finished_count = sum(row["status"] == "completed" for row in branches)
        status = ("completed" if branches and finished_count == len(branches)
                  else "partial_completed" if finished_count
                  else "in_progress" if any(row["status"] == "in_progress" for row in branches) else "not_started")
        data["project"]["status"] = status
        data["summary"] = {
            "missing_drawings": len(missing_drawings),
            "missing_suppliers": sum(task["procurement_mode"] != "made" and not task["supplier_id"]
                                     for task in tasks.values() if task["id"] in task_ids),
            "overdue": sum(bool(row["planned_date"] and row["planned_date"] < today and row["status"] != "completed")
                           for row in [*tasks.values(), *branches]),
            "unfinished": sum(task["status"] != "completed" for task in tasks.values() if task["id"] in task_ids),
        }
        return data

    def list_projects(self, *, query="", status="", include_archived=False):
        query = str(query).casefold().strip()[:200]
        result = []
        with self.unit_of_work_factory() as unit:
            for row in unit.repository.rows("projects"):
                if row["archived"] and not include_archived:
                    continue
                aggregate = self._decorate(unit.repository.export_aggregate(row["id"]), unit.repository)
                if query and query not in " ".join(str(value) for value in [row["core_number"], row["title"],
                                                    row["owner_username"], *(b["code"] for b in aggregate["branches"])]).casefold():
                    continue
                if status and aggregate["project"]["status"] != status:
                    continue
                result.append({**aggregate["project"], "summary": aggregate["summary"]})
        return sorted(result, key=lambda row: (row["updated_at"], row["id"]), reverse=True)

    def update_project(self, project_id, data, *, expected_version, actor):
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="更新开发单") as repo:
            current = repo.get("projects", project_id)
            repo.update("projects", project_id, clean_fields("project", dict(data), current=current))
        return self.detail(project_id)

    def add_branch(self, project_id, data, *, expected_version, actor):
        values = clean_fields("branch", dict(data))
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="新增开发成品") as repo:
            repo.insert("branches", new_record({**values, "project_id": project_id}, actor=actor))
        return self.detail(project_id)

    def update_branch(self, project_id, branch_id, data, *, expected_version, actor):
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="更新开发成品") as repo:
            current = require_record(repo, "branches", branch_id, project_id)
            values = clean_fields("branch", dict(data), current=current)
            repo.invalidate_confirmations(project_id, [branch_id])
            repo.update("branches", branch_id, {**values, "updated_at": now_text()})
        return self.detail(project_id)

    def add_component(self, project_id, branch_id, data, *, expected_version, actor):
        data = dict(data)
        values = clean_fields("component", data)
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="新增开发组件") as repo:
            require_record(repo, "branches", branch_id, project_id)
            task_id = data.get("task_id")
            if task_id:
                require_record(repo, "tasks", task_id, project_id)
            else:
                task_values = clean_fields("task", {"name": values["name"], **dict(data.get("task") or {})})
                if task_values.get("supplier_id"):
                    require_record(repo, "suppliers", task_values["supplier_id"])
                task = new_record({**task_values, "project_id": project_id}, actor=actor)
                repo.insert("tasks", task)
                task_id = task["id"]
            self._validate_parent(repo, project_id, branch_id, values.get("parent_id"))
            values = self._resolve_borrowing(repo, values, actor=actor)
            repo.insert("components", new_record({**values, "project_id": project_id, "branch_id": branch_id,
                                                   "task_id": task_id}, actor=actor))
            repo.invalidate_confirmations(project_id, [branch_id])
        return self.detail(project_id)

    @staticmethod
    def _validate_parent(repo, project_id, branch_id, parent_id):
        if parent_id:
            parent = require_record(repo, "components", parent_id, project_id)
            if parent["branch_id"] != branch_id:
                fail("父组件必须属于同一成品。")

    def update_component(self, project_id, component_id, data, *, expected_version, actor):
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="更新开发组件") as repo:
            current = require_record(repo, "components", component_id, project_id)
            values = clean_fields("component", dict(data), current=current)
            values = self._resolve_borrowing(repo, values, current=current, actor=actor)
            self._validate_parent(repo, project_id, current["branch_id"], values.get("parent_id"))
            repo.update("components", component_id, {**values, "updated_at": now_text()})
            repo.invalidate_confirmations(project_id, [current["branch_id"]])
        return self.detail(project_id)

    def _resolve_borrowing(self, repo, values, *, current=None, actor):
        current = current or {}
        code = values.get("borrowed_code", current.get("borrowed_code", ""))
        selected_id = values.get("borrowed_source_id")
        if selected_id:
            source = require_record(repo, "sources", selected_id)
            if code and code != source["code"]:
                fail("借用编号与所选来源快照不一致。")
            values.update(borrowed_source_id=selected_id, borrowed_code=source["code"])
        elif not code:
            values.update(borrowed_source_id=None, borrowed_code="")
        elif code == current.get("borrowed_code") and current.get("borrowed_source_id"):
            values["borrowed_source_id"] = current["borrowed_source_id"]
        else:
            source = {"id": str(uuid4()), "code": code, "name": values.get("name", current.get("name", "")),
                      "created_by": actor, "created_at": now_text()}
            repo.insert("sources", source)
            values["borrowed_source_id"] = source["id"]
        return values

    def list_sources(self):
        with self.unit_of_work_factory() as unit:
            return unit.repository.rows("sources")

    def update_task(self, project_id, task_id, data, *, expected_version, actor):
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="更新开发任务") as repo:
            current = require_record(repo, "tasks", task_id, project_id)
            values = clean_fields("task", dict(data), current=current)
            if values.get("supplier_id"):
                require_record(repo, "suppliers", values["supplier_id"])
            repo.update("tasks", task_id, {**values, "updated_at": now_text()})
            branch_ids = [row["branch_id"] for row in repo.rows("components", project_id=project_id)
                          if row["task_id"] == task_id and not row["archived"]]
            repo.invalidate_confirmations(project_id, branch_ids)
        return self.detail(project_id)

    def add_operation(self, project_id, task_id, data, *, expected_version, actor):
        values = clean_fields("operation", dict(data))
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="新增开发工序") as repo:
            require_record(repo, "tasks", task_id, project_id)
            repo.insert("operations", new_record({**values, "project_id": project_id, "task_id": task_id}, actor=actor))
            repo.invalidate_confirmations(project_id)
        return self.detail(project_id)

    def update_operation(self, project_id, operation_id, data, *, expected_version, actor):
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="更新开发工序") as repo:
            current = require_record(repo, "operations", operation_id, project_id)
            repo.update("operations", operation_id, {**clean_fields("operation", dict(data), current=current),
                                                       "updated_at": now_text()})
            repo.invalidate_confirmations(project_id)
        return self.detail(project_id)

    def confirm_branch(self, project_id, branch_id, *, expected_version, actor):
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="负责人确认成品量产完成") as repo:
            branch = require_record(repo, "branches", branch_id, project_id)
            if not branch["owner_username"] or branch["owner_username"] != actor:
                fail("请由该成品负责人本人确认完成。", code="development.owner_confirmation_required")
            if not branch["production_ready"]:
                fail("量产条件具备后才能确认完成。")
            if branch["drawing_required"] and not self._has_drawing(project_id, "branch", branch_id):
                fail("请先关联成品的已发布图纸修订，或填写不需图原因。")
            aggregate = repo.export_aggregate(project_id)
            ids = {row["task_id"] for row in aggregate["components"]
                   if row["branch_id"] == branch_id and not row["archived"]}
            tasks = [row for row in aggregate["tasks"] if row["id"] in ids]
            if not tasks or any(row["status"] != "completed" or row["archived"] for row in tasks):
                fail("该成品仍有未完成的组件开发任务。")
            if any(row["procurement_mode"] != "made" and not row["supplier_id"] for row in tasks):
                fail("请先确定外购或委外供应商。")
            if any(row["drawing_required"] and not self._has_drawing(project_id, "task", row["id"]) for row in tasks):
                fail("请先关联所有需图零件的已发布图纸修订。")
            if any(row["task_id"] in ids and not row["archived"] and row["status"] != "completed"
                   for row in aggregate["operations"]):
                fail("该成品仍有未完成工序。")
            repo.update("branches", branch_id, {"confirmed_by": actor, "confirmed_at": now_text(),
                                                "actual_date": branch["actual_date"] or date.today().isoformat(), "updated_at": now_text()})
        return self.detail(project_id)

    def archive_project(self, project_id, *, expected_version, actor):
        with self._mutation(project_id, expected_version=expected_version, actor=actor, action="归档开发单") as repo:
            repo.update("projects", project_id, {"archived": 1})
        return self.detail(project_id)

    def list_suppliers(self, *, include_archived=False):
        with self.unit_of_work_factory() as unit:
            return [row for row in unit.repository.rows("suppliers") if include_archived or not row["archived"]]

    def save_supplier(self, data, *, supplier_id=None, expected_version=None, actor):
        if not str(actor or "").strip():
            fail("写入需要已认证的操作人。")
        with self.unit_of_work_factory() as unit:
            repo = unit.repository
            unit.begin()
            current = require_record(repo, "suppliers", supplier_id) if supplier_id else None
            values = clean_fields("supplier", dict(data), current=current)
            if current:
                supplier_id = str(current["id"])
                if str(expected_version) != str(current["version"]):
                    fail("供应商资料已变化，请刷新后重试。", code="development.version_conflict")
                repo.update("suppliers", supplier_id, {**values, "version": current["version"] + 1, "updated_at": now_text()})
            else:
                record = new_record(values, actor=actor)
                supplier_id = str(record["id"])
                repo.insert("suppliers", record)
            result = repo.get("suppliers", supplier_id)
            repo.audit("维护开发供应商", supplier_id, audit_detail(current, result), actor=actor)
            unit.commit()
            return result
