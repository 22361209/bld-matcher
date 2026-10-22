from __future__ import annotations

import hashlib
import io
import sqlite3
from uuid import uuid4

import pytest
from werkzeug.datastructures import FileStorage

from app.modules.development.document_domain import DevelopmentDocumentError
from app.modules.development.document_repository import SQLiteDevelopmentDocumentRepository
from app.modules.development.document_schema import create_schema
from app.modules.development.document_service import DevelopmentDocumentService
from app.modules.development.domain import DevelopmentValidationError
from app.modules.development.file_store import DevelopmentFileStore
from app.modules.development.repository import SQLiteDevelopmentUnitOfWork
from app.modules.development.schema import install_schema
from app.modules.development.service import DevelopmentService


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def upload(content=PDF, name="QD8461.pdf", content_type="application/pdf"):
    return FileStorage(stream=io.BytesIO(content), filename=name, content_type=content_type)


@pytest.fixture
def development(tmp_path):
    database = tmp_path / "test.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE audit_logs(id INTEGER PRIMARY KEY,action TEXT,target_type TEXT,
                           target_key TEXT,actor TEXT,detail TEXT,created_at TEXT)""")
        install_schema(connection)
        create_schema(connection)
    def factory():
        return SQLiteDevelopmentUnitOfWork(database)
    core = DevelopmentService(factory)
    files = DevelopmentDocumentService(factory, DevelopmentFileStore(tmp_path / "files"),
                                       legacy_roots={"product": tmp_path / "legacy"})
    data = core.create_project({"core_number": "8461", "title": "8461开发"}, actor="owner")
    project_id = data["project"]["id"]
    data = core.add_branch(project_id, {"code": "K8461LA", "side": "left", "owner_username": "owner"},
                           actor="owner", expected_version=1)
    branch_id = data["branches"][0]["id"]
    data = core.add_component(project_id, branch_id, {"code": "QD8461", "borrowed_code": "QD8070", "name": "球销"},
                              actor="owner", expected_version=2)
    task_id = data["tasks"][0]["id"]
    return files, core, database, project_id, branch_id, task_id


def metadata(task_id=None, **kwargs):
    result = {"kind": "drawing", "number": "QD8461", "title": "球销图纸", "label": "A", "change_note": "首次登记", "state": "released"}
    if task_id:
        result.update(target_type="task", target_id=task_id)
    return dict(result, **kwargs)


def test_upload_fixed_revision_and_manual_adoption(development):
    files, core, _, project_id, _, task_id = development
    first = files.upload(project_id, metadata(task_id), upload(), actor="owner", expected_version=3)
    document_id = first["document_id"]
    second = files.upload(project_id, metadata(document_id=document_id, label="B"), upload(PDF + b"\n"), actor="owner", expected_version=4)
    document = files.list_documents(project_id, target_type="task", target_id=task_id)[0]
    assert document["links"][0]["revision_id"] == first["id"]
    assert document["links"][0]["has_newer_revision"] is True
    assert "storage_path" not in document["revisions"][0]
    assert files.file_payload(project_id, first["id"]).path.read_bytes() == PDF
    files.adopt(project_id, second["id"], target_type="task", target_id=task_id, actor="owner", expected_version=5)
    assert files.list_documents(project_id)[0]["links"][0]["revision_id"] == second["id"]
    assert core.detail(project_id)["project"]["version"] == 6


def test_same_uuid_different_bytes_refused_and_compensation_preserves_existing(tmp_path):
    store = DevelopmentFileStore(tmp_path / "files")
    project_id, revision_id = str(uuid4()), str(uuid4())
    first = store.prepare(upload(), project_id=project_id, revision_id=revision_id)
    assert store.promote(first) is True
    identical = store.prepare(upload(), project_id=project_id, revision_id=revision_id)
    assert store.promote(identical) is False
    store.compensate(identical)
    assert first.destination_path.read_bytes() == PDF
    different = store.prepare(upload(PDF + b"different"), project_id=project_id, revision_id=revision_id)
    with pytest.raises(DevelopmentDocumentError, match="拒绝覆盖"):
        store.promote(different)
    store.compensate(different)
    assert first.destination_path.read_bytes() == PDF


def test_commit_failure_removes_only_new_file_and_rolls_back(monkeypatch, development):
    files, core, database, project_id, _, task_id = development
    first = files.upload(project_id, metadata(task_id), upload(), actor="owner", expected_version=3)
    original = files.file_payload(project_id, first["id"]).path
    def broken_commit(self):
        raise OSError("synthetic commit failure")
    monkeypatch.setattr(SQLiteDevelopmentUnitOfWork, "commit", broken_commit)
    with pytest.raises(OSError, match="synthetic"):
        files.upload(project_id, metadata(document_id=first["document_id"]), upload(PDF + b"\n"), actor="owner", expected_version=4)
    assert original.read_bytes() == PDF
    assert list(files.storage.root.glob("*/*/*")) == [original]
    assert core.detail(project_id)["project"]["version"] == 4
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM development_document_revisions").fetchone()[0] == 1


@pytest.mark.parametrize("name,content,mimetype", [
    ("malicious.exe", b"MZ...", "application/octet-stream"),
    ("fake.pdf", b"<script>alert(1)</script>", "application/pdf"),
    ("incomplete.pdf", b"%PDF-1.4", "application/pdf"),
    ("fake.pdf", PDF, "text/html"),
    ("fake.step", b"MZbad", "application/octet-stream"),
])
def test_upload_whitelist_failure_leaves_no_database_or_file_changes(development, name, content, mimetype):
    files, core, _, project_id, _, task_id = development
    with pytest.raises(DevelopmentDocumentError):
        files.upload(project_id, metadata(task_id), upload(content, name, mimetype), actor="owner", expected_version=3)
    assert core.detail(project_id)["project"]["version"] == 3
    assert files.list_documents(project_id) == []
    assert not list(files.storage.root.glob("*/*/*"))


def test_stale_write_compensates_staged_upload(development):
    files, core, _, project_id, _, task_id = development
    files.upload(project_id, metadata(task_id), upload(), actor="owner", expected_version=3)
    with pytest.raises(DevelopmentValidationError):
        files.upload(project_id, metadata(task_id), upload(), actor="owner", expected_version=3)
    assert len(files.list_documents(project_id)) == 1
    assert not list((files.storage.root / ".staging").iterdir())
    assert core.detail(project_id)["project"]["version"] == 4


def test_state_change_is_new_immutable_revision_and_archive_retains_files(development):
    files, _, database, project_id, _, task_id = development
    first = files.upload(project_id, metadata(task_id, kind="process", state="draft"),
                         upload("工艺说明".encode(), "process.txt", "text/plain"), actor="owner", expected_version=3)
    published = files.set_state(project_id, first["id"], "released", change_note="发布", actor="owner", expected_version=4)
    assert published["id"] != first["id"]
    assert published["sha256"] == first["sha256"]
    assert not files.file_payload(project_id, published["id"]).previewable
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE development_document_revisions SET state='released' WHERE id=?", (first["id"],))
    files.archive(project_id, first["document_id"], actor="owner", expected_version=5)
    assert files.file_payload(project_id, first["id"]).path.is_file()
    assert files.list_documents(project_id)[0]["archived"] == 1


def test_shared_task_links_survive_reopening_and_copy_keeps_fixed_revision(development):
    files, _, database, project_id, _, task_id = development
    first = files.upload(project_id, metadata(task_id), upload(), actor="owner", expected_version=3)
    with SQLiteDevelopmentUnitOfWork(database) as work:
        task = work.repository.get("tasks", task_id)
        independent_id = str(uuid4())
        work.repository.insert("tasks", dict(task, id=independent_id))
        documents = SQLiteDevelopmentDocumentRepository(work.connection)
        documents.copy_task_links(project_id, {task_id: [independent_id]}, actor="owner")
        work.commit()
    reopened = DevelopmentDocumentService(lambda: SQLiteDevelopmentUnitOfWork(database), files.storage)
    docs = reopened.list_documents(project_id)
    assert len(docs[0]["links"]) == 2
    assert {link["revision_id"] for link in docs[0]["links"]} == {first["id"]}
    assert len({link["id"] for link in docs[0]["links"]}) == 2


def test_snapshot_is_stable_after_legacy_file_replacement(development, tmp_path):
    files, _, _, project_id, _, task_id = development
    root = tmp_path / "legacy"
    root.mkdir()
    source = root / "QD8070.pdf"
    source.write_bytes(PDF)
    result = files.snapshot_legacy(project_id, metadata(task_id), source_kind="product", relative_name=source.name,
                                   actor="owner", expected_version=3)
    source.write_bytes(PDF + b"changed")
    assert files.file_payload(project_id, result["id"]).path.read_bytes() == PDF
    assert result["sha256"] == hashlib.sha256(PDF).hexdigest()
    with pytest.raises(DevelopmentDocumentError):
        files.snapshot_legacy(project_id, metadata(task_id), source_kind="product", relative_name="../secret.pdf",
                              actor="owner", expected_version=4)


def test_unadopted_revision_does_not_clear_confirmation_but_adoption_does(development):
    files, _, database, project_id, branch_id, task_id = development
    first = files.upload(project_id, metadata(task_id), upload(), actor="owner", expected_version=3)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE development_branches SET confirmed_by='owner',actual_date='2026-10-02' WHERE id=?", (branch_id,))
    second = files.set_state(project_id, first["id"], "released", change_note="重新发布", actor="owner", expected_version=4)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT confirmed_by FROM development_branches WHERE id=?", (branch_id,)).fetchone()[0] == "owner"
    files.adopt(project_id, second["id"], target_type="task", target_id=task_id, actor="owner", expected_version=5)
    with sqlite3.connect(database) as connection:
        assert tuple(connection.execute("SELECT confirmed_by,actual_date FROM development_branches WHERE id=?", (branch_id,)).fetchone()) == ("", "")


def test_audit_failure_prevents_upload_publication(monkeypatch, development):
    files, core, _, project_id, _, task_id = development
    def broken_audit(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(SQLiteDevelopmentDocumentRepository, "audit", broken_audit)
    with pytest.raises(RuntimeError, match="audit failure"):
        files.upload(project_id, metadata(task_id), upload(), actor="owner", expected_version=3)
    assert files.list_documents(project_id) == []
    assert not list(files.storage.root.glob("*/*/*"))
    assert core.detail(project_id)["project"]["version"] == 3


def test_completion_drawing_verifier_checks_actual_adopted_file(development):
    files, _, _, project_id, _, task_id = development
    assert not files.has_valid_released_drawing(project_id, "task", task_id)
    first = files.upload(project_id, metadata(task_id), upload(), actor="owner", expected_version=3)
    assert files.has_valid_released_drawing(project_id, "task", task_id)
    path = files.file_payload(project_id, first["id"]).path
    path.write_bytes(PDF + b"tampered")
    assert not files.has_valid_released_drawing(project_id, "task", task_id)
    path.unlink()
    assert not files.has_valid_released_drawing(project_id, "task", task_id)
