from __future__ import annotations

import json
import sqlite3

import pytest

from app.modules.development.document_schema import create_schema
from app.modules.development.domain import DevelopmentValidationError
from app.modules.development.repository import SQLiteDevelopmentRepository, SQLiteDevelopmentUnitOfWork
from app.modules.development.schema import install_schema
from app.modules.development.service import DevelopmentService


@pytest.fixture
def grid(tmp_path):
    path = tmp_path / "grid-synthetic.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE audit_logs(id INTEGER PRIMARY KEY,action TEXT,target_type TEXT,
                           target_key TEXT,actor TEXT,detail TEXT,created_at TEXT)""")
        install_schema(connection)
        create_schema(connection)
    service = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(path))
    data = service.create_project({"core_number": "8461", "title": "合成树形BOM"}, actor="owner")
    data = service.add_branch(data["project"]["id"], {"code": "K8461LA", "owner_username": "owner",
                              "drawing_required": False, "drawing_reason": "测试免图"},
                              expected_version=data["project"]["version"], actor="owner")
    return service, data, data["branches"][0]["id"], path


def save(service, data, branch_id, rows, **kwargs):
    return service.save_component_grid(data["project"]["id"], branch_id, rows,
                                        expected_version=data["project"]["version"], actor="owner", **kwargs)


def by_id(rows, identifier):
    return next(row for row in rows if row["id"] == identifier)


def test_unordered_new_tree_is_one_version_and_one_audit(grid):
    service, initial, branch_id, path = grid
    result = save(service, initial, branch_id, [
        {"client_id": "pin", "parent_client_id": "ball", "name": "球销", "code": "QD8461", "borrowed_code": "QD8070"},
        {"client_id": "boot", "parent_client_id": "ball", "name": "防尘罩"},
        {"client_id": "ball", "name": "球头"},
        {"client_id": "arm", "name": "臂体"},
    ])
    data, mapping = result["detail"], result["client_ids"]
    assert data["project"]["version"] == initial["project"]["version"] + 1
    assert len(data["tasks"]) == 4 and len(mapping) == 4
    assert by_id(data["components"], mapping["pin"])["parent_id"] == mapping["ball"]
    assert by_id(data["components"], mapping["boot"])["parent_id"] == mapping["ball"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_logs WHERE action='批量保存开发组件表'").fetchone()[0] == 1
        detail = json.loads(connection.execute("SELECT detail FROM audit_logs ORDER BY id DESC LIMIT 1").fetchone()[0])
        assert not detail["before"]["components"] and len(detail["after"]["components"]) == 4
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_dirty_row_patch_preserves_low_frequency_fields_and_missing_rows(grid):
    service, initial, branch_id, _ = grid
    first = save(service, initial, branch_id, [
        {"client_id": "a", "name": "臂体", "quantity": 2, "position": "内侧", "notes": "组件备注", "task": {
            "owner_username": "alice", "material_category": "锻铝", "material_grade": "6061",
            "forming_process": "热锻", "procurement_mode": "subcontracted", "notes": "工艺说明",
            "drawing_required": False, "drawing_reason": "沿用标准"}},
        {"client_id": "b", "name": "衬套"},
    ])
    identifier = first["client_ids"]["a"]
    result = save(service, first["detail"], branch_id,
                  [{"id": identifier, "code": "ARM-USER-INPUT", "task": {"planned_date": "2026-12-01"}}])["detail"]
    item = by_id(result["components"], identifier)
    task = by_id(result["tasks"], item["task_id"])
    assert len(result["components"]) == 2
    assert (item["quantity"], item["position"], item["notes"]) == (2, "内侧", "组件备注")
    assert (task["material_category"], task["material_grade"], task["forming_process"]) == ("锻铝", "6061", "热锻")
    assert task["owner_username"] == "alice" and task["notes"] == "工艺说明"
    assert task["procurement_mode"] == "subcontracted" and not task["drawing_required"]


def test_same_codes_never_implicitly_share_tasks_or_borrowing_sources(grid):
    service, data, branch_id, _ = grid
    result = save(service, data, branch_id, [
        {"client_id": "one", "name": "球销", "code": "QD8461", "borrowed_code": "QD8070"},
        {"client_id": "two", "name": "球销", "code": "QD8461", "borrowed_code": "QD8070"},
    ])["detail"]
    assert len({item["task_id"] for item in result["components"]}) == 2
    assert len({item["borrowed_source_id"] for item in result["components"]}) == 2
    assert all(not task["owner_username"] and not task["actual_date"] for task in result["tasks"])


def test_existing_shared_task_updates_once_and_merges_distinct_fields(grid):
    service, data, branch_id, _ = grid
    first = save(service, data, branch_id, [{"client_id": "one", "name": "球销"}])
    data = first["detail"]
    original = data["components"][0]
    data = service.add_component(data["project"]["id"], branch_id, {"name": "第二处", "task_id": original["task_id"]},
                                 expected_version=data["project"]["version"], actor="owner")
    second = next(row for row in data["components"] if row["id"] != original["id"])
    result = save(service, data, branch_id, [
        {"id": original["id"], "task": {"status": "completed", "owner_username": "owner"}},
        {"id": second["id"], "task": {"actual_date": "2026-10-02"}},
    ])["detail"]
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["status"] == "completed"
    assert result["tasks"][0]["actual_date"] == "2026-10-02"
    assert not result["branches"][0]["confirmed_by"]


def test_shared_task_conflicting_patch_rolls_back_all_rows(grid):
    service, data, branch_id, _ = grid
    first = save(service, data, branch_id, [{"client_id": "one", "name": "球销"}])
    data = first["detail"]
    original = data["components"][0]
    data = service.add_component(data["project"]["id"], branch_id, {"name": "第二处", "task_id": original["task_id"]},
                                 expected_version=data["project"]["version"], actor="owner")
    with pytest.raises(DevelopmentValidationError) as error:
        save(service, data, branch_id, [
            {"id": data["components"][0]["id"], "code": "should rollback", "task": {"planned_date": "2026-12-01"}},
            {"id": data["components"][1]["id"], "task": {"planned_date": "2026-12-02"}},
        ])
    assert error.value.code == "development.shared_task_conflict"
    assert service.detail(data["project"]["id"]) == data


def test_supplier_requires_explicit_creation_and_one_batch_reuses_created_unit(grid):
    service, data, branch_id, _ = grid
    with pytest.raises(DevelopmentValidationError, match="明确确认"):
        save(service, data, branch_id, [{"client_id": "a", "name": "臂体", "task": {"supplier_name": "新责任单位"}}])
    assert not service.list_suppliers()
    result = save(service, data, branch_id, [
        {"client_id": "a", "name": "臂体", "task": {"supplier_name": "新责任单位", "create_supplier": True}},
        {"client_id": "b", "name": "衬套", "task": {"supplier_name": "新责任单位", "create_supplier": True}},
    ])["detail"]
    assert len(result["suppliers"]) == 1 and len(service.list_suppliers()) == 1
    assert len({task["supplier_id"] for task in result["tasks"]}) == 1


def test_existing_supplier_selected_by_uuid_and_never_guessed_by_name(grid):
    service, data, branch_id, _ = grid
    supplier = service.save_supplier({"name": "已有单位"}, actor="owner")
    with pytest.raises(DevelopmentValidationError, match="同名"):
        save(service, data, branch_id, [{"client_id": "a", "name": "衬套", "task": {
            "supplier_name": "已有单位", "create_supplier": True}}])
    result = save(service, data, branch_id, [{"client_id": "a", "name": "衬套", "task": {
        "supplier_name": "已有单位", "supplier_id": supplier["id"]}}])["detail"]
    assert result["tasks"][0]["supplier_id"] == supplier["id"]


def test_completion_keeps_existing_requirements_and_drawing_flags_are_not_files(grid):
    service, data, branch_id, _ = grid
    with pytest.raises(DevelopmentValidationError, match="负责人"):
        save(service, data, branch_id, [{"client_id": "a", "name": "臂体", "task": {"status": "completed"}}])
    result = save(service, data, branch_id, [{"client_id": "a", "name": "臂体", "task": {
        "status": "completed", "owner_username": "owner", "actual_date": "2026-10-02", "drawing_status": "ready"}}])["detail"]
    assert result["summary"]["missing_drawings"] == 1
    assert result["branches"][0]["status"] != "completed" and not result["branches"][0]["confirmed_by"]


def test_reopening_shared_task_clears_dates_and_confirmations_on_both_branches(grid):
    service, data, branch_id, _ = grid
    data = save(service, data, branch_id, [{"client_id": "a", "name": "衬套", "task": {
        "owner_username": "owner", "actual_date": "2026-10-02", "status": "completed",
        "drawing_required": False, "drawing_reason": "标准件"}}])["detail"]
    item = data["components"][0]
    data = service.copy_branch(data["project"]["id"], branch_id, {"code": "K8461RA"}, modes={item["id"]: "shared"},
                                expected_version=data["project"]["version"], actor="owner")
    for branch in list(data["branches"]):
        data = service.update_branch(data["project"]["id"], branch["id"], {"production_ready": True},
                                     expected_version=data["project"]["version"], actor="owner")
        data = service.confirm_branch(data["project"]["id"], branch["id"], expected_version=data["project"]["version"], actor="owner")
    assert all(branch["confirmed_by"] for branch in data["branches"])
    result = save(service, data, branch_id, [{"id": item["id"], "task": {"status": "in_progress"}}])["detail"]
    assert all(not branch["confirmed_by"] and not branch["actual_date"] for branch in result["branches"])
    assert not result["tasks"][0]["actual_date"]


def test_late_failure_rolls_back_suppliers_sources_rows_and_audit(grid, monkeypatch):
    service, data, branch_id, _ = grid
    def failed_audit(*args, **kwargs):
        raise RuntimeError("injected audit failure")
    monkeypatch.setattr(SQLiteDevelopmentRepository, "audit", failed_audit)
    with pytest.raises(RuntimeError):
        save(service, data, branch_id, [{"client_id": "a", "name": "衬套", "borrowed_code": "B-1",
                                        "task": {"supplier_name": "新单位", "create_supplier": True}}])
    assert service.detail(data["project"]["id"]) == data
    assert not service.list_suppliers() and not service.list_sources()


def test_noop_keeps_confirmation_and_does_not_delete_missing_components(grid):
    service, data, branch_id, _ = grid
    result = save(service, data, branch_id, [{"client_id": "a", "name": "衬套", "task": {
        "owner_username": "owner", "actual_date": "2026-10-02", "status": "completed",
        "drawing_required": False, "drawing_reason": "标准件"}}])
    data = result["detail"]
    data = service.update_branch(data["project"]["id"], branch_id, {"production_ready": True},
                                 expected_version=data["project"]["version"], actor="owner")
    data = service.confirm_branch(data["project"]["id"], branch_id, expected_version=data["project"]["version"], actor="owner")
    final = save(service, data, branch_id, [{"id": result["client_ids"]["a"], "name": "衬套"}])["detail"]
    assert final["branches"][0]["confirmed_by"] == "owner"


@pytest.mark.parametrize("rows", [[], [{}], [{"client_id": "a", "name": "one"}, {"client_id": "a", "name": "two"}],
                                  [{"client_id": "a", "name": "one", "task_id": "forbidden"}],
                                  [{"client_id": "a", "name": "one", "task": {"create_supplier": "true"}}]])
def test_malformed_batches_never_change_version(grid, rows):
    service, data, branch_id, _ = grid
    with pytest.raises(DevelopmentValidationError):
        save(service, data, branch_id, rows)
    assert service.detail(data["project"]["id"]) == data
