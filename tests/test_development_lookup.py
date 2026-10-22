from __future__ import annotations

import hashlib
import io
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from flask import Flask, g
from werkzeug.datastructures import FileStorage

from app.modules.development.document_schema import create_schema
from app.modules.development.document_service import DevelopmentDocumentService
from app.modules.development.domain import DevelopmentValidationError
from app.modules.development.file_store import DevelopmentFileStore
from app.modules.development.lookup_repository import open_lookup_repository
from app.modules.development.lookup_service import DevelopmentLookupService
from app.modules.development.repository import SQLiteDevelopmentUnitOfWork
from app.modules.development.schema import install_schema
from app.modules.development.service import DevelopmentService
from app.modules.development.web_lookup import register


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


@pytest.fixture
def fixture(tmp_path):
    database = tmp_path / "lookup.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE audit_logs(id INTEGER PRIMARY KEY,action TEXT,target_type TEXT,
                           target_key TEXT,actor TEXT,detail TEXT,created_at TEXT)""")
        install_schema(connection)
        create_schema(connection)
    def work():
        return SQLiteDevelopmentUnitOfWork(database)
    core = DevelopmentService(work)
    files = DevelopmentDocumentService(work, DevelopmentFileStore(tmp_path / "files"))
    supplier = core.save_supplier({"name": "合成球销供应商"}, actor="owner")
    project_id = core.create_project({"core_number": "8461", "title": "合成开发资料"}, actor="owner")["project"]["id"]
    data = core.add_branch(project_id, {"code": "K8461LA", "side": "left"}, actor="owner", expected_version=1)
    branch_id = data["branches"][0]["id"]
    data = core.add_component(project_id, branch_id, {"code": "QD8461", "borrowed_code": "QD8070", "name": "球销",
        "task": {"owner_username": "owner", "status": "completed", "planned_date": "2026-09-01", "actual_date": "2026-09-02",
                 "procurement_mode": "purchased", "supplier_id": supplier["id"], "material_grade": "40Cr",
                 "forming_process": "锻造", "notes": "合成任务说明", "drawing_required": 0, "drawing_reason": "无需图纸"}},
        actor="owner", expected_version=2)
    component = data["components"][0]
    lookup = DevelopmentLookupService(lambda: open_lookup_repository(database))
    return SimpleNamespace(database=database, core=core, files=files, lookup=lookup, project_id=project_id,
                           branch_id=branch_id, component_id=component["id"], task_id=component["task_id"],
                           source_id=component["borrowed_source_id"], supplier_id=supplier["id"])


def version(fixture):
    return fixture.core.detail(fixture.project_id)["project"]["version"]


def add_component(fixture, **values):
    return fixture.core.add_component(fixture.project_id, fixture.branch_id, {"name": "第二零件", **values},
                                      actor="owner", expected_version=version(fixture))


def test_stable_source_uuid_and_safe_suggestions_are_read_only(fixture):
    before = hashlib.sha256(fixture.database.read_bytes()).hexdigest()
    result = fixture.lookup.search_sources(fixture.project_id, query="qd8070", component_id=fixture.component_id)
    candidate = result["candidates"][0]
    assert candidate["id"] == fixture.source_id
    assert candidate["code"] == "QD8070"
    suggestion = candidate["task_suggestions"][0]
    assert suggestion["task_id"] == fixture.task_id
    assert suggestion["component_code"] == "QD8461"
    assert suggestion["fields"]["supplier_id"] == fixture.supplier_id
    assert suggestion["fields"]["material_grade"] == "40Cr"
    assert suggestion["fields"]["development_mode"] == "borrowed"
    assert not set(suggestion["fields"]) & {"id", "code", "task_id", "status", "actual_date", "planned_date", "owner_username",
                                           "drawing_status", "drawing_required", "revision_id", "production_ready"}
    assert result["project_version"] == 3
    assert hashlib.sha256(fixture.database.read_bytes()).hexdigest() == before


def test_same_code_sources_stay_distinct_and_never_infer_task_sharing(fixture):
    second = add_component(fixture, code="QDOTHER", borrowed_code="QD8070")
    source_ids = {row["borrowed_source_id"] for row in second["components"]}
    assert len(source_ids) == 2
    candidates = fixture.lookup.search_sources(fixture.project_id, query="QD8070")["candidates"]
    assert {row["id"] for row in candidates} == source_ids
    assert all(len(row["task_suggestions"]) == 1 for row in candidates)
    assert len({row["task_suggestions"][0]["task_id"] for row in candidates}) == 2


def test_shared_task_has_one_suggestion_even_with_multiple_component_occurrences(fixture):
    add_component(fixture, code="QD8461R", task_id=fixture.task_id, borrowed_source_id=fixture.source_id)
    candidate = fixture.lookup.search_sources(fixture.project_id, query="QD8070")["candidates"][0]
    assert len(candidate["task_suggestions"]) == 1
    assert candidate["task_suggestions"][0]["task_id"] == fixture.task_id


def test_literal_sql_wildcards_and_source_limit(fixture):
    add_component(fixture, code="OWN", borrowed_code="QD%_8070")
    result = fixture.lookup.search_sources(fixture.project_id, query="%_")
    assert [row["code"] for row in result["candidates"]] == ["QD%_8070"]
    assert fixture.lookup.search_sources(fixture.project_id, query="' OR 1=1 --")["candidates"] == []
    result = fixture.lookup.search_sources(fixture.project_id, query="QD", limit=1)
    assert len(result["candidates"]) == 1
    assert result["has_more"] is True


def test_archived_tasks_and_suppliers_do_not_offer_active_details(fixture):
    with sqlite3.connect(fixture.database) as connection:
        connection.execute("UPDATE development_suppliers SET archived=1 WHERE id=?", (fixture.supplier_id,))
    candidate = fixture.lookup.search_sources(fixture.project_id, query="QD8070")["candidates"][0]
    assert candidate["task_suggestions"][0]["fields"]["supplier_id"] is None
    assert candidate["task_suggestions"][0]["supplier_name"] == ""
    with sqlite3.connect(fixture.database) as connection:
        connection.execute("UPDATE development_tasks SET archived=1 WHERE id=?", (fixture.task_id,))
    candidate = fixture.lookup.search_sources(fixture.project_id, query="QD8070")["candidates"][0]
    assert candidate["id"] == fixture.source_id
    assert candidate["task_suggestions"] == []


@pytest.mark.parametrize("changes", [{"query": "x" * 101}, {"query": "QD\x00"}, {"limit": 0}, {"limit": 51}, {"limit": True}])
def test_search_input_bounds(fixture, changes):
    with pytest.raises(DevelopmentValidationError):
        fixture.lookup.search_sources(fixture.project_id, **changes)


def test_foreign_component_cannot_be_used_as_context(fixture):
    other = fixture.core.create_project({"core_number": "9000", "title": "另一开发单"}, actor="owner")["project"]["id"]
    with pytest.raises(DevelopmentValidationError, match="不属于"):
        fixture.lookup.search_sources(other, component_id=fixture.component_id)
    with pytest.raises(DevelopmentValidationError, match="不属于"):
        fixture.lookup.component_context(other, fixture.component_id)
    with pytest.raises(DevelopmentValidationError, match="不存在"):
        fixture.lookup.search_sources(str(uuid4()))


def test_component_context_keeps_own_code_and_the_adopted_old_revision(fixture):
    first = fixture.files.upload(fixture.project_id, {"kind": "drawing", "number": "QD8461", "title": "球销图纸",
        "label": "A", "state": "released", "change_note": "首次保存", "target_type": "task", "target_id": fixture.task_id},
        FileStorage(stream=io.BytesIO(PDF), filename="drawing.pdf", content_type="application/pdf"),
        actor="owner", expected_version=version(fixture))
    fixture.files.upload(fixture.project_id, {"document_id": first["document_id"], "label": "B", "state": "released",
        "change_note": "另存新修订"}, FileStorage(stream=io.BytesIO(PDF + b"\n"), filename="drawing-B.pdf", content_type="application/pdf"),
        actor="owner", expected_version=version(fixture))
    context = fixture.lookup.component_context(fixture.project_id, fixture.component_id)
    assert context["upload_defaults"] == {"number": "QD8461", "title": "球销", "target": f"task|{fixture.task_id}"}
    assert context["documents"][0]["revision_id"] == first["id"]
    assert context["documents"][0]["label"] == "A"
    assert context["documents"][0]["has_newer_revision"] is True
    assert context["documents"][0]["previewable"] is True
    assert not set(context["documents"][0]) & {"storage_path", "path", "price", "tax_price"}


def test_missing_database_is_not_created(tmp_path):
    missing = tmp_path / "never-created.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        with open_lookup_repository(missing):
            pass
    assert not missing.exists()


@pytest.fixture
def web(fixture):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic", GRANTED={"view_development", "view_development_files"})
    @app.before_request
    def user():
        granted = app.config["GRANTED"]
        g.user = {"username": "synthetic", "permissions": granted} if granted is not None else None
    app.add_url_rule("/development/<project_id>", "development_detail", lambda project_id: "")
    app.add_url_rule("/development/<project_id>/documents/<revision_id>/download", "development_download_document", lambda **values: "")
    app.add_url_rule("/development/<project_id>/documents/<revision_id>/preview", "development_preview_document", lambda **values: "")
    register(app)
    with patch("app.modules.development.web_lookup.get_development_lookup_service", return_value=fixture.lookup) as factory:
        yield app, app.test_client(), factory


def test_lookup_web_permissions_and_no_legacy_scope_access(fixture, web):
    app, client, factory = web
    source_url = f"/development/{fixture.project_id}/lookup/sources?q=QD8070&source_type=materials"
    context_url = f"/development/{fixture.project_id}/lookup/components/{fixture.component_id}"
    headers = {"Accept": "application/json"}
    app.config["GRANTED"] = None
    assert client.get(source_url, headers=headers).status_code == 401
    app.config["GRANTED"] = set()
    assert client.get(source_url, headers=headers).status_code == 403
    factory.assert_not_called()
    app.config["GRANTED"] = {"view_development"}
    response = client.get(source_url, headers=headers)
    assert response.status_code == 200
    assert response.json["candidates"][0]["id"] == fixture.source_id
    assert response.headers["Cache-Control"] == "private, no-store"
    assert client.get(context_url, headers=headers).status_code == 403
    app.config["GRANTED"] = {"view_development", "view_development_files"}
    response = client.get(context_url, headers=headers)
    assert response.status_code == 200
    assert f"component={fixture.component_id}" in response.json["documents_url"]


def test_lookup_web_errors_are_stable_and_do_not_expose_paths(fixture, web):
    _, client, _ = web
    headers = {"Accept": "application/json"}
    assert client.get(f"/development/{fixture.project_id}/lookup/sources?limit=999", headers=headers).status_code == 400
    assert client.get(f"/development/{uuid4()}/lookup/sources", headers=headers).status_code == 404
    with patch.object(fixture.lookup, "search_sources", side_effect=OSError("/private/secret.sqlite3")):
        response = client.get(f"/development/{fixture.project_id}/lookup/sources", headers=headers)
    assert response.status_code == 500
    assert response.json["code"] == "development.lookup_failed"
    assert "secret" not in response.get_data(as_text=True)
