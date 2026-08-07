from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app.modules.customer_drawings.domain import CustomerDrawingValidationError
from app.modules.customer_drawings.infrastructure import CustomerDrawingFileStore
from app.modules.customer_drawings.repository import SQLiteCustomerDrawingUnitOfWork
from app.modules.customer_drawings.service import CustomerDrawingService


CUSTOMER_SYNC_ID = "a" * 64
PNG_PAYLOAD = b"\x89PNG\r\n\x1a\n" + b"drawing-pixels" * 8


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
            CREATE TABLE customer_drawing_groups (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL REFERENCES customers(id),
              sync_id TEXT NOT NULL UNIQUE,
              bld_no TEXT NOT NULL DEFAULT '',
              direction TEXT NOT NULL CHECK(direction IN ('customer','issued')),
              title TEXT NOT NULL DEFAULT '',
              drawing_no TEXT NOT NULL DEFAULT '',
              current_version INTEGER NOT NULL DEFAULT 0,
              archived INTEGER NOT NULL DEFAULT 0,
              created_by TEXT NOT NULL DEFAULT '',
              updated_by TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE customer_drawing_files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              group_id INTEGER NOT NULL REFERENCES customer_drawing_groups(id),
              sync_id TEXT NOT NULL UNIQUE,
              version_no INTEGER NOT NULL,
              revision_label TEXT NOT NULL DEFAULT '',
              original_name TEXT NOT NULL,
              storage_path TEXT NOT NULL UNIQUE,
              content_type TEXT NOT NULL,
              size_bytes INTEGER NOT NULL DEFAULT 0,
              sha256 TEXT NOT NULL DEFAULT '',
              uploaded_by TEXT NOT NULL DEFAULT '',
              note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE(group_id, version_no)
            );
            INSERT INTO customers (id, name, sync_id) VALUES (1, '测试客户', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
            """
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def drawing_env(tmp_path: Path) -> tuple[CustomerDrawingService, Path, Path]:
    database_path = tmp_path / "drawings.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _schema(database_path)
    service = CustomerDrawingService(
        lambda: SQLiteCustomerDrawingUnitOfWork(database_path, connection_factory=_connect),
        CustomerDrawingFileStore(storage_root, max_file_bytes=1024 * 1024),
    )
    return service, database_path, storage_root


def _upload(name: str, payload: bytes, content_type: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(payload), filename=name, content_type=content_type)


def test_groups_create_update_and_list_by_direction(drawing_env) -> None:
    service, database_path, _storage_root = drawing_env

    incoming = service.create(
        1,
        {"direction": "customer", "title": "支架总成图纸", "bld_no": "k8053", "drawing_no": "CUST-001"},
        actor="007",
    )
    issued = service.create(
        1,
        {"direction": "issued", "title": "衬套加工图", "bld_no": "", "drawing_no": "BLD-2026-01"},
        actor="007",
    )
    assert incoming.current_version == 0
    assert incoming.direction_label == "客户来图"
    assert incoming.bld_no == "K8053"
    assert issued.direction_label == "我方出图"

    groups = service.list_for_customer(1)
    assert [group.direction for group in groups] == ["customer", "issued"]

    updated = service.update(
        1,
        incoming.id,
        {"direction": "customer", "title": "支架总成图纸（改）", "bld_no": "K8053", "drawing_no": "CUST-002"},
        actor="007",
    )
    assert updated.title == "支架总成图纸（改）"
    assert updated.drawing_no == "CUST-002"
    assert service.summaries_for_customers([1, 99])[1].payload() == {"group_count": 2, "file_count": 0}
    assert service.summaries_for_customers([1, 99])[99].group_count == 0

    connection = _connect(database_path)
    try:
        actions = [row["action"] for row in connection.execute("SELECT action FROM audit_logs ORDER BY id")]
    finally:
        connection.close()
    assert actions == ["新增客户图纸", "新增客户图纸", "更新客户图纸"]


def test_versions_increment_with_revision_note_and_verified_payload(drawing_env) -> None:
    service, database_path, storage_root = drawing_env
    group = service.create(
        1,
        {
            "direction": "customer",
            "title": "支架总成图纸",
            "bld_no": "K8053",
            "drawing_no": "CUST-001",
            "revision_label": "Rev A",
        },
        files=(_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
        actor="007",
    )
    assert group.current_version == 1
    assert group.current_file is not None
    assert group.current_file.revision_label == "Rev A"
    assert group.current_file.storage_path.startswith(f"{CUSTOMER_SYNC_ID}/drawings/{group.sync_id}/v0001/")

    version_two = service.add_version(
        1,
        group.id,
        (_upload("支架-v2.png", PNG_PAYLOAD + b"v2", "image/png"),),
        revision_label="Rev B",
        note="按客户 8 月意见修改",
        actor="007",
    )
    assert version_two.current_version == 2
    assert [version.version_no for version in version_two.versions] == [2, 1]
    current = version_two.versions[0]
    assert current.revision_label == "Rev B"
    assert current.note == "按客户 8 月意见修改"
    assert current.file.uploaded_by == "007"

    payload = service.file_payload(1, current.file.id, actor="007", for_preview=True)
    assert payload.path.read_bytes() == PNG_PAYLOAD + b"v2"
    assert payload.content_type == "image/png"
    assert payload.previewable is True
    assert payload.path.is_relative_to(storage_root)

    connection = _connect(database_path)
    try:
        stored_paths = [row[0] for row in connection.execute("SELECT storage_path FROM customer_drawing_files")]
        actions = [row[0] for row in connection.execute("SELECT action FROM audit_logs ORDER BY id")]
    finally:
        connection.close()
    assert all(not Path(path).is_absolute() and ".." not in Path(path).parts for path in stored_paths)
    # GET preview/download stay read-only; only explicit mutations are audited.
    assert actions == ["新增客户图纸", "上传客户图纸版本"]
    assert not list(storage_root.rglob("*.tmp"))


def test_upload_validation_rejects_extension_signature_size_and_unsafe_paths(drawing_env) -> None:
    service, database_path, storage_root = drawing_env
    base_data = {"direction": "issued", "title": "测试图纸", "bld_no": "", "drawing_no": ""}

    with pytest.raises(CustomerDrawingValidationError) as extension_error:
        service.create(
            1,
            base_data,
            files=(_upload("notes.txt", b"plain text", "text/plain"),),
            actor="007",
        )
    assert extension_error.value.code == "customer_drawing.extension_not_allowed"

    with pytest.raises(CustomerDrawingValidationError) as signature_error:
        service.create(
            1,
            base_data,
            files=(_upload("not-a-pdf.pdf", b"plain text", "application/pdf"),),
            actor="007",
        )
    assert signature_error.value.code == "customer_drawing.invalid_file_content"

    tiny_store = CustomerDrawingFileStore(storage_root, max_file_bytes=4)
    with pytest.raises(CustomerDrawingValidationError) as size_error:
        tiny_store.prepare(
            (_upload("large.png", PNG_PAYLOAD, "image/png"),),
            customer_sync_id=CUSTOMER_SYNC_ID,
            group_sync_id="b" * 32,
            version_no=1,
        )
    assert size_error.value.code == "customer_drawing.file_too_large"

    with pytest.raises(CustomerDrawingValidationError) as path_error:
        tiny_store.resolve(f"{CUSTOMER_SYNC_ID}/drawings/../../secret.png", customer_sync_id=CUSTOMER_SYNC_ID)
    assert path_error.value.code == "customer_drawing.unsafe_path"

    connection = _connect(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_groups").fetchone()[0] == 0
    finally:
        connection.close()


def test_archive_and_unarchive_control_mutations_and_downloads(drawing_env) -> None:
    service, _database_path, _storage_root = drawing_env
    group = service.create(
        1,
        {"direction": "customer", "title": "支架总成图纸", "bld_no": "", "drawing_no": ""},
        files=(_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
        actor="007",
    )
    archived = service.archive(1, group.id, actor="007")
    assert archived.archived is True
    assert service.list_for_customer(1) == []
    assert service.list_for_customer(1, include_archived=True)[0].archived is True

    with pytest.raises(CustomerDrawingValidationError) as add_error:
        service.add_version(
            1,
            group.id,
            (_upload("支架-v2.png", PNG_PAYLOAD, "image/png"),),
            actor="007",
        )
    assert add_error.value.code == "customer_drawing.archived"
    with pytest.raises(CustomerDrawingValidationError) as update_error:
        service.update(
            1,
            group.id,
            {"direction": "customer", "title": "改名", "bld_no": "", "drawing_no": ""},
            actor="007",
        )
    assert update_error.value.code == "customer_drawing.archived"
    with pytest.raises(CustomerDrawingValidationError) as file_error:
        service.file_payload(1, group.files[0].id, actor="007")
    assert file_error.value.code == "customer_drawing.archived"
    assert service.file_payload(1, group.files[0].id, actor="007", allow_archived=True).path.is_file()

    restored = service.unarchive(1, group.id, actor="007")
    assert restored.archived is False
    version_two = service.add_version(
        1,
        group.id,
        (_upload("支架-v2.png", PNG_PAYLOAD, "image/png"),),
        actor="007",
    )
    assert version_two.current_version == 2


def test_database_commit_failure_compensates_promoted_files(tmp_path: Path) -> None:
    database_path = tmp_path / "drawings.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _schema(database_path)

    class FailingCommitUnitOfWork(SQLiteCustomerDrawingUnitOfWork):
        def commit(self) -> None:
            raise sqlite3.OperationalError("forced commit failure")

    service = CustomerDrawingService(
        lambda: FailingCommitUnitOfWork(database_path, connection_factory=_connect),
        CustomerDrawingFileStore(storage_root),
    )
    with pytest.raises(sqlite3.OperationalError, match="forced commit failure"):
        service.create(
            1,
            {"direction": "issued", "title": "衬套加工图", "bld_no": "", "drawing_no": ""},
            files=(_upload("衬套-v1.png", PNG_PAYLOAD, "image/png"),),
            actor="007",
        )

    connection = _connect(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_groups").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0
    finally:
        connection.close()
    assert not storage_root.exists() or not any(storage_root.rglob("*"))


def test_version_claim_conflict_discards_staged_upload(tmp_path: Path) -> None:
    database_path = tmp_path / "drawings.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _schema(database_path)

    class ConflictingClaimUnitOfWork(SQLiteCustomerDrawingUnitOfWork):
        def __enter__(self) -> "ConflictingClaimUnitOfWork":
            unit_of_work = super().__enter__()
            assert unit_of_work.connection is not None

            class ConflictingClaimRepository(type(unit_of_work.repository)):
                def claim_next_version(
                    self,
                    customer_id: int,
                    group_id: int,
                    *,
                    expected_version: int,
                    actor: str,
                ) -> None:
                    return None

            unit_of_work.repository = ConflictingClaimRepository(unit_of_work.connection)
            return unit_of_work

    service = CustomerDrawingService(
        lambda: ConflictingClaimUnitOfWork(database_path, connection_factory=_connect),
        CustomerDrawingFileStore(storage_root, max_file_bytes=1024 * 1024),
    )
    group = service.create(
        1,
        {"direction": "customer", "title": "支架总成图纸", "bld_no": "", "drawing_no": ""},
        files=(_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
        actor="007",
    )
    assert group.current_version == 1

    with pytest.raises(CustomerDrawingValidationError) as conflict_error:
        service.add_version(
            1,
            group.id,
            (_upload("支架-v2.png", PNG_PAYLOAD + b"v2", "image/png"),),
            actor="007",
        )
    assert conflict_error.value.code == "customer_drawing.version_conflict"

    connection = _connect(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 1
        assert connection.execute(
            "SELECT current_version FROM customer_drawing_groups WHERE id = ?", (group.id,)
        ).fetchone()[0] == 1
    finally:
        connection.close()
    # 版本占位失败后暂存文件被 discard：磁盘上只剩 v1 一个已落库文件。
    files_on_disk = [path for path in storage_root.rglob("*") if path.is_file()]
    assert len(files_on_disk) == 1
    assert "/v0001/" in files_on_disk[0].as_posix()


def test_file_payload_rejects_tampered_storage_content(drawing_env) -> None:
    service, _database_path, storage_root = drawing_env
    group = service.create(
        1,
        {"direction": "customer", "title": "支架总成图纸", "bld_no": "", "drawing_no": ""},
        files=(_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
        actor="007",
    )
    assert group.current_file is not None

    stored = next(path for path in storage_root.rglob("*.png") if path.is_file())
    stored.write_bytes(b"tampered-content")

    with pytest.raises(CustomerDrawingValidationError) as corrupt_error:
        service.file_payload(1, group.current_file.id, actor="007")
    assert corrupt_error.value.code == "customer_drawing.file_corrupt"


def test_migration_035_creates_all_three_tables_with_constraints(tmp_path: Path) -> None:
    from app.migrations import _add_customer_drawings

    database_path = tmp_path / "migration-035.sqlite3"
    connection = _connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE quote_records (id INTEGER PRIMARY KEY, customer_name TEXT NOT NULL);
            INSERT INTO customers (id, name) VALUES (1, '测试客户');
            INSERT INTO quote_records (id, customer_name) VALUES (1, '测试客户');
            """
        )
        _add_customer_drawings(connection)
        # 幂等：重复执行不报错。
        _add_customer_drawings(connection)

        connection.execute(
            """
            INSERT INTO customer_drawing_groups
              (customer_id, sync_id, direction, title, created_at, updated_at)
            VALUES (1, 'g-sync', 'customer', '支架图纸', '2026-08-03', '2026-08-03')
            """
        )
        connection.execute(
            """
            INSERT INTO customer_drawing_files
              (group_id, sync_id, version_no, original_name, storage_path, content_type, created_at)
            VALUES (1, 'f-sync', 1, 'a.png', 'c/drawings/g/v0001/f-a.png', 'image/png', '2026-08-03')
            """
        )
        connection.execute(
            "INSERT INTO quote_record_drawings (quote_record_id, drawing_file_id, created_at) VALUES (1, 1, '2026-08-03')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO quote_record_drawings (quote_record_id, drawing_file_id, created_at) VALUES (1, 1, '2026-08-03')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO customer_drawing_files
                  (group_id, sync_id, version_no, original_name, storage_path, content_type, created_at)
                VALUES (1, 'f-sync-2', 1, 'b.png', 'c/drawings/g/v0001/f2-b.png', 'image/png', '2026-08-03')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO customer_drawing_groups
                  (customer_id, sync_id, direction, title, created_at, updated_at)
                VALUES (1, 'g-sync-2', 'sideways', '非法方向', '2026-08-03', '2026-08-03')
                """
            )
        indexes = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }
        assert {
            "idx_customer_drawing_groups_customer",
            "idx_customer_drawing_files_group",
            "idx_quote_record_drawings_quote",
            "idx_quote_record_drawings_file",
        }.issubset(indexes)
    finally:
        connection.close()
