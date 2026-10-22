from __future__ import annotations

import copy
import sqlite3
from uuid import uuid4

import pytest

from app.modules.development.domain import DevelopmentValidationError, validate_aggregate
from app.modules.development.repository import SQLiteDevelopmentRepository, SQLiteDevelopmentUnitOfWork
from app.modules.development.schema import install_schema
from app.modules.development.service import DevelopmentService


@pytest.fixture
def setup(tmp_path):
    from app.modules.development.document_schema import create_schema
    path = tmp_path / "synthetic.sqlite3"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE audit_logs (id INTEGER PRIMARY KEY,action TEXT,target_type TEXT,
                       target_key TEXT,detail TEXT,actor TEXT,created_at TEXT)""")
    install_schema(connection)
    create_schema(connection)
    connection.commit()
    connection.close()
    service = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(path))
    return service, path


def project(service):
    return service.create_project({"core_number": "8461", "title": "8461 左右摆臂", "owner_username": "alice"}, actor="alice")


def mutate(service, state, name, *args, **kwargs):
    return getattr(service, name)(state["project"]["id"], *args, expected_version=state["project"]["version"],
                                  actor="alice", **kwargs)


def with_component(service, *, complete=False):
    state = project(service)
    state = mutate(service, state, "add_branch", {"code": "K8461LA", "side": "left", "owner_username": "alice",
                                                 "drawing_required": False, "drawing_reason": "标准成品免图"})
    branch = state["branches"][0]
    state = mutate(service, state, "add_component", branch["id"], {
        "name": "球销", "code": "QD8461", "borrowed_code": "QD8070", "quantity": 2,
        "task": {"drawing_required": False, "drawing_reason": "已验证的现成标准件", "owner_username": "alice",
                 "development_mode": "borrowed", "procurement_mode": "made",
                 "status": "completed" if complete else "not_started", "actual_date": "2026-10-01" if complete else ""},
    })
    return state, branch["id"]


def test_additive_migration_idempotent_preserves_existing(setup):
    service, path = setup
    state = project(service)
    with sqlite3.connect(path) as connection:
        install_schema(connection)
        install_schema(connection)
        assert connection.execute("SELECT count(*) FROM development_projects").fetchone()[0] == 1
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert service.detail(state["project"]["id"])["project"]["core_number"] == "8461"


def test_codes_dimensions_materials_remain_independent(setup):
    service, _ = setup
    state, _ = with_component(service)
    item, task = state["components"][0], state["tasks"][0]
    state = mutate(service, state, "update_task", task["id"], {"material_category": "锻铝", "material_grade": "6061",
                                                            "forming_process": "热锻", "procurement_mode": "subcontracted"})
    assert (item["code"], item["borrowed_code"]) == ("QD8461", "QD8070")
    assert state["tasks"][0]["development_mode"] == "borrowed"
    assert state["tasks"][0]["material_grade"] == "6061"
    assert state["summary"]["missing_suppliers"] == 1


def test_shared_task_survives_reload_and_codes_remain_local(setup):
    service, path = setup
    state, branch_id = with_component(service)
    item = state["components"][0]
    state = mutate(service, state, "copy_branch", branch_id, {"code": "K8461RA", "side": "right"},
                   modes={item["id"]: "shared"})
    assert len(state["tasks"]) == 1
    assert {row["task_id"] for row in state["components"]} == {item["task_id"]}
    state = mutate(service, state, "update_task", item["task_id"], {"status": "completed", "actual_date": "2026-10-01"})
    restarted = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(path))
    assert restarted.detail(state["project"]["id"])["tasks"][0]["status"] == "completed"
    right = next(row for row in state["components"] if row["id"] != item["id"])
    state = mutate(service, state, "update_component", right["id"], {"code": "RIGHT-LOCAL", "borrowed_code": "SRC-R"})
    original = next(row for row in state["components"] if row["id"] == item["id"])
    assert (original["code"], original["borrowed_code"]) == ("QD8461", "QD8070")


def test_independent_copy_resets_progress_dates_and_operations(setup):
    service, _ = setup
    state, branch_id = with_component(service, complete=True)
    item = state["components"][0]
    state = mutate(service, state, "add_operation", item["task_id"], {
        "sequence": 1, "name": "检验", "owner_username": "alice", "status": "completed", "actual_date": "2026-10-01"})
    state = mutate(service, state, "copy_branch", branch_id, {"code": "K8461RA", "side": "right", "production_ready": True},
                   modes={item["id"]: "independent"})
    copied_task = next(row for row in state["tasks"] if row["id"] != item["task_id"])
    assert copied_task["status"] == "not_started" and not copied_task["actual_date"]
    copied_operation = next(row for row in state["operations"] if row["task_id"] == copied_task["id"])
    assert copied_operation["status"] == "not_started" and not copied_operation["actual_date"]
    assert all(not row["production_ready"] for row in state["branches"])


def test_copy_requires_each_choice_and_explicit_full_code(setup):
    service, _ = setup
    state, branch_id = with_component(service)
    with pytest.raises(DevelopmentValidationError):
        mutate(service, state, "copy_branch", branch_id, {"code": "K8461RA"}, modes={})
    with pytest.raises(DevelopmentValidationError):
        mutate(service, state, "copy_branch", branch_id, {"side": "right"}, modes={state["components"][0]["id"]: "shared"})
    assert service.detail(state["project"]["id"])["project"]["version"] == state["project"]["version"]


def test_hierarchy_cycle_and_cross_branch_rejected_atomically(setup):
    service, _ = setup
    state, branch_id = with_component(service)
    parent = state["components"][0]
    state = mutate(service, state, "add_component", branch_id, {"name": "防尘罩", "parent_id": parent["id"]})
    child = next(row for row in state["components"] if row["id"] != parent["id"])
    with pytest.raises(DevelopmentValidationError, match="循环"):
        mutate(service, state, "update_component", parent["id"], {"parent_id": child["id"]})
    state = mutate(service, state, "add_branch", {"code": "K8461RA"})
    right = next(row for row in state["branches"] if row["id"] != branch_id)
    with pytest.raises(DevelopmentValidationError):
        mutate(service, state, "add_component", right["id"], {"name": "错误节点", "parent_id": parent["id"]})
    assert len(service.detail(state["project"]["id"])["tasks"]) == 2


def test_cross_project_shared_task_rejected(setup):
    service, _ = setup
    first, _ = with_component(service)
    state = project(service)
    state = mutate(service, state, "add_branch", {"code": "OTHER"})
    with pytest.raises(DevelopmentValidationError):
        mutate(service, state, "add_component", state["branches"][0]["id"], {"name": "错误", "task_id": first["tasks"][0]["id"]})


def test_version_conflict_prevents_lost_update(setup):
    service, _ = setup
    state = project(service)
    mutate(service, state, "update_project", {"title": "first"})
    with pytest.raises(DevelopmentValidationError) as error:
        mutate(service, state, "update_project", {"title": "stale"})
    assert error.value.code == "development.version_conflict"
    assert service.detail(state["project"]["id"])["project"]["title"] == "first"


def test_partial_completion_owner_gate_and_shared_reopening(setup):
    service, _ = setup
    state, branch_id = with_component(service, complete=True)
    item = state["components"][0]
    state = mutate(service, state, "copy_branch", branch_id, {"code": "K8461RA", "owner_username": "alice"},
                   modes={item["id"]: "shared"})
    with pytest.raises(DevelopmentValidationError, match="量产"):
        mutate(service, state, "confirm_branch", branch_id)
    state = mutate(service, state, "update_branch", branch_id, {"production_ready": True})
    with pytest.raises(DevelopmentValidationError, match="本人"):
        service.confirm_branch(state["project"]["id"], branch_id, expected_version=state["project"]["version"], actor="bob")
    state = mutate(service, state, "confirm_branch", branch_id)
    assert state["project"]["status"] == "partial_completed"
    state = mutate(service, state, "update_task", item["task_id"], {"status": "in_progress"})
    assert all(not branch["confirmed_by"] for branch in state["branches"])
    assert not state["tasks"][0]["actual_date"]


def test_completion_needs_real_released_drawing_and_completed_operation(setup):
    service, _ = setup
    state, branch_id = with_component(service, complete=True)
    state = mutate(service, state, "update_branch", branch_id, {"production_ready": True})
    state = mutate(service, state, "update_task", state["tasks"][0]["id"], {"drawing_required": True, "drawing_status": "ready"})
    assert state["summary"]["missing_drawings"] == 1
    with pytest.raises(DevelopmentValidationError, match="图纸"):
        mutate(service, state, "confirm_branch", branch_id)
    state = mutate(service, state, "update_task", state["tasks"][0]["id"], {"drawing_required": False})
    state = mutate(service, state, "add_operation", state["tasks"][0]["id"], {"sequence": 1, "name": "未完成工序"})
    with pytest.raises(DevelopmentValidationError, match="工序"):
        mutate(service, state, "confirm_branch", branch_id)


def test_archiving_preserves_history_and_blocks_edit(setup):
    service, _ = setup
    state, _ = with_component(service)
    state = mutate(service, state, "archive_project")
    assert len(state["tasks"]) == 1
    assert not service.list_projects()
    assert len(service.list_projects(include_archived=True)) == 1
    with pytest.raises(DevelopmentValidationError, match="归档"):
        mutate(service, state, "update_project", {"title": "overwrite"})


def test_audit_failure_rolls_back_entire_write(setup, monkeypatch):
    service, _ = setup
    state = project(service)
    def fail_audit(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(SQLiteDevelopmentRepository, "audit", fail_audit)
    with pytest.raises(RuntimeError):
        mutate(service, state, "add_branch", {"code": "K8461LA"})
    actual = service.detail(state["project"]["id"])
    assert not actual["branches"] and actual["project"]["version"] == state["project"]["version"]


def test_sync_roundtrip_idempotent_and_rejects_bad_dependency(setup):
    service, path = setup
    state, _ = with_component(service)
    with SQLiteDevelopmentUnitOfWork(path) as unit:
        raw = unit.repository.export_aggregate(state["project"]["id"])
        unit.repository.import_aggregate(raw)
        unit.repository.import_aggregate(raw)
        unit.commit()
    assert len(service.detail(state["project"]["id"])["tasks"]) == 1
    bad = copy.deepcopy(raw)
    bad["components"][0]["task_id"] = str(uuid4())
    with pytest.raises(DevelopmentValidationError, match="引用缺失"):
        validate_aggregate(bad)


def test_invalid_codes_dates_numbers_rejected(setup):
    service, _ = setup
    with pytest.raises(DevelopmentValidationError, match="四位"):
        service.create_project({"core_number": "84611", "title": "bad"}, actor="alice")
    state, branch_id = with_component(service)
    with pytest.raises(DevelopmentValidationError):
        mutate(service, state, "add_component", branch_id, {"name": "bad", "quantity": float("nan")})
    with pytest.raises(DevelopmentValidationError):
        mutate(service, state, "update_project", {"planned_date": "2026-99-01"})


def test_recorded_actual_date_is_kept_at_responsible_confirmation(setup):
    service, _ = setup
    state, branch_id = with_component(service, complete=True)
    state = mutate(service, state, "update_branch", branch_id, {"production_ready": True, "actual_date": "2026-09-20"})
    state = mutate(service, state, "confirm_branch", branch_id)
    assert state["branches"][0]["actual_date"] == "2026-09-20"
    assert state["project"]["status"] == "completed"


@pytest.mark.parametrize("field,value", [("version", "3"), ("archived", "0"), ("planned_date", "20261002"),
                                        ("unknown_column", "payload")])
def test_sync_rejects_unnormalized_or_unknown_record_fields(setup, field, value):
    service, path = setup
    state, _ = with_component(service)
    with SQLiteDevelopmentUnitOfWork(path) as unit:
        data = unit.repository.export_aggregate(state["project"]["id"])
    data["project"][field] = value
    with pytest.raises(DevelopmentValidationError):
        validate_aggregate(data)


def test_sync_rejects_incomplete_record_before_import(setup):
    service, path = setup
    state = project(service)
    with SQLiteDevelopmentUnitOfWork(path) as unit:
        data = unit.repository.export_aggregate(state["project"]["id"])
    del data["project"]["version"]
    with pytest.raises(DevelopmentValidationError, match="必要字段"):
        validate_aggregate(data)


def test_supplier_unknown_and_empty_actor_fail_without_partial_component(setup):
    service, _ = setup
    state, branch_id = with_component(service)
    with pytest.raises(DevelopmentValidationError):
        service.save_supplier({"name": "not trusted"}, actor="")
    with pytest.raises(DevelopmentValidationError):
        mutate(service, state, "add_component", branch_id, {"name": "missing supplier", "task": {"supplier_id": str(uuid4())}})
    assert len(service.detail(state["project"]["id"])["components"]) == 1


def test_audit_before_and_after_include_actual_previous_version(setup):
    import json
    service, path = setup
    state = project(service)
    result = mutate(service, state, "update_project", {"title": "revised"})
    with sqlite3.connect(path) as connection:
        detail = json.loads(connection.execute("SELECT detail FROM audit_logs ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert detail["before"]["project"]["version"] == state["project"]["version"]
    assert detail["after"]["project"]["version"] == result["project"]["version"]


def test_borrowing_source_identity_survives_edits_and_same_code_is_not_unique(setup):
    service, path = setup
    state, branch_id = with_component(service)
    original = state["components"][0]
    original_source = state["sources"][0]
    assert original["borrowed_source_id"] == original_source["id"]
    state = mutate(service, state, "add_component", branch_id, {"name": "第二个来源", "borrowed_code": "QD8070"})
    assert len(state["sources"]) == 2 and len({row["id"] for row in state["sources"]}) == 2
    state = mutate(service, state, "update_component", original["id"], {"borrowed_code": "QD9000"})
    changed = next(row for row in state["components"] if row["id"] == original["id"])
    assert changed["borrowed_source_id"] != original_source["id"]
    with SQLiteDevelopmentUnitOfWork(path) as unit:
        assert unit.repository.get("sources", original_source["id"]) == original_source
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            unit.repository.update("sources", original_source["id"], {"code": "overwrite"})


def test_choose_existing_borrowing_source_and_copy_preserves_uuid(setup):
    service, _ = setup
    state, branch_id = with_component(service)
    original = state["components"][0]
    state = mutate(service, state, "add_component", branch_id,
                   {"name": "明确选择同来源", "borrowed_source_id": original["borrowed_source_id"]})
    assert all(row["borrowed_code"] == "QD8070" for row in state["components"])
    assert len(state["sources"]) == 1
    source_ids = {row["id"]: row["borrowed_source_id"] for row in state["components"]}
    state = mutate(service, state, "copy_branch", branch_id, {"code": "K8461RA"},
                   modes={row["id"]: "independent" for row in state["components"]})
    assert {row["borrowed_source_id"] for row in state["components"]} == set(source_ids.values())
    assert next(row for row in state["branches"] if row["id"] != branch_id)["owner_username"] == "alice"


def test_sync_refuses_existing_source_uuid_with_different_content(setup):
    service, path = setup
    state, _ = with_component(service)
    with SQLiteDevelopmentUnitOfWork(path) as unit:
        data = unit.repository.export_aggregate(state["project"]["id"])
        data["sources"][0]["name"] = "attempt replacement"
        with pytest.raises(DevelopmentValidationError) as error:
            unit.repository.import_aggregate(data)
        assert error.value.code == "development.source_identity_conflict"


def test_sync_refuses_missing_borrowing_uuid_or_mismatched_source(setup):
    service, path = setup
    state, _ = with_component(service)
    with SQLiteDevelopmentUnitOfWork(path) as unit:
        data = unit.repository.export_aggregate(state["project"]["id"])
    data["components"][0]["borrowed_source_id"] = None
    with pytest.raises(DevelopmentValidationError, match="永久"):
        validate_aggregate(data)
    data["components"][0]["borrowed_source_id"] = data["sources"][0]["id"]
    data["sources"][0]["code"] = "different"
    with pytest.raises(DevelopmentValidationError, match="不一致"):
        validate_aggregate(data)
