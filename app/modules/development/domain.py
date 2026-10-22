from __future__ import annotations

import math
import json
import re
from datetime import date
from typing import NoReturn
from uuid import UUID


class DevelopmentValidationError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = ""):
        super().__init__(message)
        self.code, self.message, self.field = code, message, field


STATUSES = ("not_started", "in_progress", "completed")
MATERIAL_CATEGORIES = ("冲压", "铸铁", "锻铁", "铸铝", "锻铝", "复合材料")
ENUMS = {
    "status": STATUSES, "side": ("left", "right", "single"),
    "development_mode": ("new", "borrowed", "selected"),
    "procurement_mode": ("made", "purchased", "subcontracted"),
    "drawing_status": ("not_started", "in_progress", "ready"),
}
FIELDS = {
    "project": ("core_number", "title", "owner_username", "planned_date", "actual_date", "notes"),
    "branch": ("code", "name", "side", "owner_username", "planned_date", "actual_date", "notes", "production_ready",
               "drawing_required", "drawing_reason", "drawing_status"),
    "component": ("parent_id", "code", "borrowed_code", "borrowed_source_id", "name", "quantity", "position", "notes", "sort_order"),
    "task": ("name", "owner_username", "planned_date", "actual_date", "status", "development_mode",
             "procurement_mode", "supplier_id", "material_category", "material_grade", "forming_process", "notes",
             "drawing_required", "drawing_reason", "drawing_status"),
    "operation": ("sequence", "name", "executor", "owner_username", "planned_date", "actual_date", "status", "notes"),
    "supplier": ("name", "contact", "phone", "notes", "archived"),
    "source": ("code", "name"),
}


def fail(message: str, *, field: str = "", code: str = "development.invalid") -> NoReturn:
    raise DevelopmentValidationError(code, message, field=field)


def clean_bool(value):
    if value in (True, 1, "1", "true", "on", "yes"):
        return 1
    if value in (False, 0, "0", "false", "off", "no", "", None):
        return 0
    fail("请选择有效的是/否选项。")


def clean_fields(kind: str, data: dict, *, current: dict | None = None) -> dict:
    values = {}
    for key in FIELDS[kind]:
        if key not in data:
            continue
        value = data[key]
        if key in {"drawing_required", "production_ready", "archived"}:
            value = clean_bool(value)
        elif key in {"parent_id", "supplier_id", "borrowed_source_id"}:
            value = str(value or "").strip() or None
            if value:
                validate_uuid(value)
        elif key == "quantity":
            try:
                value = float(value)
            except (ValueError, TypeError):
                fail("数量必须为正数。", field=key)
            if not math.isfinite(value) or not 0 < value <= 1_000_000:
                fail("数量必须大于零且不超过一百万。", field=key)
        elif key in {"sequence", "sort_order"}:
            if not re.fullmatch(r"[0-9]{1,6}", str(value)):
                fail("排序必须是非负整数。", field=key)
            try:
                value = int(value)
            except (ValueError, TypeError, OverflowError):
                fail("排序必须是整数。", field=key)
            if value < (1 if key == "sequence" else 0) or value > 100000:
                fail("排序值超出范围。", field=key)
        else:
            value = str(value or "").strip()
            if len(value) > (4000 if key in {"notes", "drawing_reason"} else 200):
                fail("内容过长。", field=key)
            if key.endswith("_date") and value:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        fail("日期必须为 YYYY-MM-DD。", field=key)
                except ValueError:
                    fail("日期必须为 YYYY-MM-DD。", field=key)
            if key in ENUMS and value not in ENUMS[key]:
                fail("选项无效。", field=key)
        values[key] = value
    merged = {**(current or {}), **values}
    required = {"project": ("title", "core_number"), "branch": ("code",), "component": ("name",),
                "task": ("name",), "operation": ("name", "sequence"), "supplier": ("name",), "source": ("code",)}[kind]
    for key in required:
        if not merged.get(key):
            fail("请填写必要信息。", field=key)
    if kind == "project" and not re.fullmatch(r"[0-9]{4}", str(merged["core_number"])):
        fail("核心系列须为四位数字；完整编码请在成品或零件中填写。", field="core_number")
    if kind in {"task", "branch"} and not merged.get("drawing_required", 1) and not merged.get("drawing_reason"):
        fail("不需图纸时请填写原因。", field="drawing_reason")
    if kind in {"task", "operation"}:
        status = merged.get("status", "not_started")
        if status == "completed" and (not merged.get("actual_date") or not merged.get("owner_username")):
            fail("完成前请填写负责人和实际完成日期。", field="actual_date")
        if status != "completed" and "status" in values:
            values["actual_date"] = ""
    return values


def validate_uuid(value):
    try:
        parsed = UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        fail("永久标识无效。")
    if str(parsed) != str(value):
        fail("永久标识必须使用标准 UUID 格式。")


def validate_aggregate(data: dict) -> None:
    """Validate all UUID relationships before sync mutates any rows or files."""
    project = data.get("project")
    if not isinstance(project, dict):
        fail("开发单内容无效。")
    validate_uuid(project.get("id"))
    clean_fields("project", project)
    _validate_record("project", project)
    project_id = project["id"]
    groups = {}
    all_ids = {project_id}
    for plural, singular in (("branches", "branch"), ("tasks", "task"), ("components", "component"),
                             ("operations", "operation"), ("suppliers", "supplier"), ("sources", "source")):
        rows = data.get(plural, [])
        if not isinstance(rows, list) or len(rows) > 10000:
            fail("开发单记录数量或格式无效。")
        groups[plural] = {}
        for row in rows:
            if not isinstance(row, dict):
                fail("开发记录格式无效。")
            validate_uuid(row.get("id"))
            if row["id"] in all_ids:
                fail("永久标识重复。")
            all_ids.add(row["id"])
            if plural not in {"suppliers", "sources"} and row.get("project_id") != project_id:
                fail("记录属于其他开发单。")
            clean_fields(singular, row)
            _validate_record(singular, row)
            if "archived" in row and row["archived"] not in (0, 1):
                fail("归档状态无效。")
            groups[plural][row["id"]] = row
    for task in groups["tasks"].values():
        if task.get("supplier_id") and task["supplier_id"] not in groups["suppliers"]:
            fail("供应商引用缺失。")
    for component in groups["components"].values():
        source_id = component.get("borrowed_source_id")
        if bool(source_id) != bool(component.get("borrowed_code")):
            fail("借用编号必须关联永久来源标识。")
        if source_id and (source_id not in groups["sources"] or groups["sources"][source_id]["code"] != component["borrowed_code"]):
            fail("借用来源缺失或编号与来源快照不一致。")
        if component.get("branch_id") not in groups["branches"] or component.get("task_id") not in groups["tasks"]:
            fail("组件成品或任务引用缺失。")
        if not component.get("archived") and (groups["tasks"][component["task_id"]].get("archived")
                                               or groups["branches"][component["branch_id"]].get("archived")):
            fail("活跃组件不能引用已归档任务或成品。")
        visited = {component["id"]}
        parent_id = component.get("parent_id")
        while parent_id:
            parent = groups["components"].get(parent_id)
            if not parent or parent["branch_id"] != component["branch_id"]:
                fail("组件父节点不存在或属于其他成品。")
            if not component.get("archived") and parent.get("archived"):
                fail("活跃组件不能引用已归档父节点。")
            if parent_id in visited:
                fail("组件树不能循环引用。")
            visited.add(parent_id)
            parent_id = parent.get("parent_id")
    for operation in groups["operations"].values():
        if operation.get("task_id") not in groups["tasks"]:
            fail("工序任务引用缺失。")
    for branch in groups["branches"].values():
        if branch.get("confirmed_by"):
            if branch["confirmed_by"] != branch.get("owner_username") or not branch.get("production_ready"):
                fail("成品完成确认与负责人或量产条件不一致。")
            if not branch.get("actual_date") or not branch.get("confirmed_at"):
                fail("成品完成确认缺少日期。")
            ids = {row["task_id"] for row in groups["components"].values()
                   if row["branch_id"] == branch["id"] and not row.get("archived")}
            if not ids or any(groups["tasks"][identifier].get("status") != "completed" for identifier in ids):
                fail("成品已确认但组件任务未完成。")
            if any(row["task_id"] in ids and not row.get("archived") and row.get("status") != "completed"
                   for row in groups["operations"].values()):
                fail("成品已确认但工序未完成。")


def _validate_record(kind, row):
    common = {"id", "created_by", "created_at"} if kind == "source" else {"id", "created_by", "created_at", "updated_at", "archived"}
    extra = {"project": {"version"}, "supplier": {"version"}, "branch": {"project_id", "confirmed_by", "confirmed_at"},
             "task": {"project_id"}, "component": {"project_id", "branch_id", "task_id"},
             "operation": {"project_id", "task_id"}, "source": set()}[kind]
    expected = set(FIELDS[kind]) | common | extra
    if set(row) != expected:
        fail("开发记录含未知字段或缺少必要字段。")
    for key in ("archived", "drawing_required", "production_ready"):
        if key in row and (type(row[key]) is not int or row[key] not in (0, 1)):
            fail("布尔状态必须为 0 或 1。")
    if "version" in row and (type(row["version"]) is not int or not 1 <= row["version"] <= 2_147_483_647):
        fail("修订版本号无效。")
    normalized = clean_fields(kind, row)
    for key, value in normalized.items():
        if key in row and value != row[key]:
            fail("开发记录字段未规范化。", field=key)


def audit_detail(before, after):
    return json.dumps({"before": before, "after": after}, ensure_ascii=False, sort_keys=True)
