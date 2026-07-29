from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app.modules.customer_documents.domain import CustomerDocumentValidationError
from app.modules.customer_documents.infrastructure import CustomerDocumentFileStore
from app.modules.customer_documents.repository import SQLiteCustomerDocumentUnitOfWork
from app.modules.customer_documents.service import CustomerDocumentService


CUSTOMER_SYNC_ID = "a" * 64


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _schema(path: Path) -> None:
    connection = _connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              sync_id TEXT NOT NULL UNIQUE
            );
            CREATE TABLE audit_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              action TEXT NOT NULL,
              target_type TEXT NOT NULL,
              target_key TEXT NOT NULL,
              actor TEXT DEFAULT '',
              detail TEXT DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE TABLE customer_document_groups (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL REFERENCES customers(id),
              sync_id TEXT NOT NULL UNIQUE,
              category TEXT NOT NULL,
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              language TEXT NOT NULL DEFAULT 'zh-CN',
              current_version INTEGER NOT NULL DEFAULT 0,
              archived INTEGER NOT NULL DEFAULT 0,
              created_by TEXT NOT NULL DEFAULT '',
              updated_by TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE customer_document_files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              group_id INTEGER NOT NULL REFERENCES customer_document_groups(id),
              sync_id TEXT NOT NULL UNIQUE,
              version_no INTEGER NOT NULL,
              original_name TEXT NOT NULL,
              storage_path TEXT NOT NULL UNIQUE,
              content_type TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              sha256 TEXT NOT NULL,
              created_by TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            INSERT INTO customers (id, name, sync_id) VALUES (1, '测试客户', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
            """
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def document_env(tmp_path: Path) -> tuple[CustomerDocumentService, Path, Path]:
    database_path = tmp_path / "documents.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _schema(database_path)
    service = CustomerDocumentService(
        lambda: SQLiteCustomerDocumentUnitOfWork(database_path, connection_factory=_connect),
        CustomerDocumentFileStore(storage_root, max_file_bytes=1024 * 1024),
    )
    return service, database_path, storage_root


def _upload(name: str, payload: bytes, content_type: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(payload), filename=name, content_type=content_type)


def _xlsx_payload() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("xl/workbook.xml", "<workbook />")
    return output.getvalue()


def test_text_only_group_can_be_created_updated_and_summarized(document_env) -> None:
    service, database_path, _storage_root = document_env

    group = service.create(
        1,
        {
            "category": "label",
            "title": "标签颜色要求",
            "description": "标签必须使用白底黑字。",
            "language": "zh-CN",
        },
        actor="007",
    )

    assert group.current_version == 0
    assert group.current_files == ()
    assert group.category_label == "标签要求"
    updated = service.update(
        1,
        group.id,
        {
            "category": "label",
            "title": "标签颜色与尺寸要求",
            "description": "标签必须使用白底黑字，尺寸以确认样为准。",
            "language": "zh-CN",
        },
        actor="007",
    )
    assert updated.title == "标签颜色与尺寸要求"
    assert service.summaries_for_customers([1, 99])[1].payload() == {
        "group_count": 1,
        "file_count": 0,
        "current_file_count": 0,
    }
    assert service.summaries_for_customers([1, 99])[99].group_count == 0

    connection = _connect(database_path)
    try:
        actions = [row["action"] for row in connection.execute("SELECT action FROM audit_logs ORDER BY id")]
    finally:
        connection.close()
    assert actions == ["新增客户资料", "更新客户资料"]


def test_file_batches_create_versions_and_return_verified_payloads(document_env) -> None:
    service, database_path, storage_root = document_env
    pdf = b"%PDF-1.4\ncustomer label\n%%EOF"
    group = service.create(
        1,
        {
            "category": "outer_box",
            "title": "外箱资料",
            "description": "",
            "language": "zh-CN",
        },
        files=(
            _upload("外箱示例.pdf", pdf, "application/pdf"),
            _upload("说明.txt", "第一版要求".encode(), "text/plain; charset=utf-8"),
        ),
        actor="007",
    )
    assert group.current_version == 1
    assert len(group.files) == 2
    assert group.versions == (1,)
    assert all(not Path(item.storage_path).is_absolute() for item in group.files)
    assert all(item.storage_path.startswith(f"{CUSTOMER_SYNC_ID}/{group.sync_id}/v0001/") for item in group.files)

    version_two = service.add_version(
        1,
        group.id,
        (_upload("packing-list.xlsx", _xlsx_payload(), "application/octet-stream"),),
        actor="007",
    )
    assert version_two.current_version == 2
    assert version_two.versions == (2, 1)
    assert len(version_two.files) == 3
    assert [item.original_name for item in version_two.current_files] == ["packing-list.xlsx"]

    pdf_record = next(item for item in version_two.files if item.original_name == "外箱示例.pdf")
    payload = service.file_payload(1, pdf_record.id, actor="007", for_preview=True)
    assert payload.path.read_bytes() == pdf
    assert payload.content_type == "application/pdf"
    assert payload.previewable is True
    assert len(payload.sha256) == 64

    xlsx_record = version_two.current_files[0]
    with pytest.raises(CustomerDocumentValidationError, match="不支持在线预览") as preview_error:
        service.file_payload(1, xlsx_record.id, actor="007", for_preview=True)
    assert preview_error.value.code == "customer_document.preview_not_supported"
    download = service.file_payload(1, xlsx_record.id, actor="007")
    assert download.path.is_relative_to(storage_root)

    connection = _connect(database_path)
    try:
        stored_paths = [row[0] for row in connection.execute("SELECT storage_path FROM customer_document_files")]
        actions = [row[0] for row in connection.execute("SELECT action FROM audit_logs ORDER BY id")]
    finally:
        connection.close()
    assert all(not Path(path).is_absolute() and ".." not in Path(path).parts for path in stored_paths)
    # GET preview/download stay read-only; only explicit mutations are audited.
    assert actions == ["新增客户资料", "上传客户资料版本"]
    assert not list(storage_root.rglob("*.tmp"))


def test_upload_validation_rejects_extension_mime_signature_size_and_unsafe_paths(document_env) -> None:
    service, database_path, storage_root = document_env
    base_data = {"category": "other", "title": "测试资料", "description": "", "language": "zh-CN"}

    with pytest.raises(CustomerDocumentValidationError) as extension_error:
        service.create(
            1,
            base_data,
            files=(_upload("payload.exe", b"MZ payload", "application/octet-stream"),),
            actor="007",
        )
    assert extension_error.value.code == "customer_document.extension_not_allowed"

    with pytest.raises(CustomerDocumentValidationError) as mime_error:
        service.create(
            1,
            base_data,
            files=(_upload("not-a-pdf.pdf", b"%PDF-1.4", "image/png"),),
            actor="007",
        )
    assert mime_error.value.code == "customer_document.content_type_mismatch"

    with pytest.raises(CustomerDocumentValidationError) as signature_error:
        service.create(
            1,
            base_data,
            files=(_upload("not-a-pdf.pdf", b"plain text", "application/pdf"),),
            actor="007",
        )
    assert signature_error.value.code == "customer_document.invalid_file_content"

    tiny_store = CustomerDocumentFileStore(storage_root, max_file_bytes=4)
    with pytest.raises(CustomerDocumentValidationError) as size_error:
        tiny_store.prepare(
            (_upload("large.txt", b"12345", "text/plain"),),
            customer_sync_id=CUSTOMER_SYNC_ID,
            group_sync_id="b" * 32,
            version_no=1,
        )
    assert size_error.value.code == "customer_document.file_too_large"

    with pytest.raises(CustomerDocumentValidationError) as path_error:
        tiny_store.resolve(f"{CUSTOMER_SYNC_ID}/../../secret.txt", customer_sync_id=CUSTOMER_SYNC_ID)
    assert path_error.value.code == "customer_document.unsafe_path"

    long_original_name = f"{'a' * 236}.pdf"
    long_name_group = service.create(
        1,
        base_data,
        files=(_upload(long_original_name, b"%PDF-1.4\n%%EOF", "application/pdf"),),
        actor="007",
    )
    stored_file = long_name_group.current_files[0]
    assert stored_file.original_name == long_original_name
    assert len(Path(stored_file.storage_path).name.encode("utf-8")) <= 255
    assert (storage_root / stored_file.storage_path).is_file()

    connection = _connect(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM customer_document_groups").fetchone()[0] == 1
    finally:
        connection.close()


def test_archive_preserves_history_and_blocks_mutations_and_default_download(document_env) -> None:
    service, _database_path, _storage_root = document_env
    group = service.create(
        1,
        {"category": "pi", "title": "PI 模板", "description": "", "language": "en"},
        files=(_upload("pi.txt", b"PI template", "text/plain"),),
        actor="007",
    )
    archived = service.archive(1, group.id, actor="007")
    assert archived.archived is True
    assert service.list_for_customer(1) == []
    assert service.list_for_customer(1, include_archived=True)[0].archived is True

    with pytest.raises(CustomerDocumentValidationError) as add_error:
        service.add_version(
            1,
            group.id,
            (_upload("pi-v2.txt", b"PI v2", "text/plain"),),
            actor="007",
        )
    assert add_error.value.code == "customer_document.archived"
    with pytest.raises(CustomerDocumentValidationError) as file_error:
        service.file_payload(1, group.files[0].id, actor="007")
    assert file_error.value.code == "customer_document.archived"
    assert service.file_payload(1, group.files[0].id, actor="007", allow_archived=True).path.is_file()


def test_database_commit_failure_compensates_promoted_files(tmp_path: Path) -> None:
    database_path = tmp_path / "documents.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _schema(database_path)

    class FailingCommitUnitOfWork(SQLiteCustomerDocumentUnitOfWork):
        def commit(self) -> None:
            raise sqlite3.OperationalError("forced commit failure")

    service = CustomerDocumentService(
        lambda: FailingCommitUnitOfWork(database_path, connection_factory=_connect),
        CustomerDocumentFileStore(storage_root),
    )
    with pytest.raises(sqlite3.OperationalError, match="forced commit failure"):
        service.create(
            1,
            {"category": "ci", "title": "CI 模板", "description": "", "language": "en"},
            files=(_upload("ci.txt", b"CI template", "text/plain"),),
            actor="007",
        )

    connection = _connect(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM customer_document_groups").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_document_files").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0
    finally:
        connection.close()
    assert not storage_root.exists() or not any(storage_root.rglob("*"))
