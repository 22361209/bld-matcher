from __future__ import annotations

import io
import sqlite3
from pathlib import Path, PurePosixPath

import pytest
from werkzeug.datastructures import FileStorage

from app.database import connect
from app.modules.customer_products.domain import (
    CatalogProductInfo,
    CustomerProductValidationError,
    QuotedProductOption,
)
from app.modules.customer_products.infrastructure import CustomerDrawingFileStore
from app.modules.customer_products.ports import (
    CatalogDrawingSource,
    CustomerFileRemovalFailure,
    StagedCustomerFileRemovalBatch,
)
from app.modules.customer_products.repository import SQLiteCustomerProductUnitOfWork
from app.modules.customer_products.service import CustomerProductService


CUSTOMER_SYNC_ID = "a" * 64
PNG_PAYLOAD = b"\x89PNG\r\n\x1a\n" + b"drawing-pixels" * 8
PDF_PAYLOAD = b"%PDF-1.4\ncatalog drawing\n%%EOF"


class FakeQuoteHistory:
    def __init__(self, options: list[QuotedProductOption]) -> None:
        self.options = options
        self.calls: list[tuple[int, str]] = []

    def quoted_products(self, customer_id: int, customer_name: str) -> list[QuotedProductOption]:
        self.calls.append((customer_id, customer_name))
        return list(self.options)


class FakeCatalog:
    def __init__(
        self,
        infos: dict[str, CatalogProductInfo] | None = None,
        drawings: dict[str, CatalogDrawingSource] | None = None,
    ) -> None:
        self.infos = infos or {}
        self.drawings = drawings or {}

    def info(self, bld_no: str) -> CatalogProductInfo | None:
        return self.infos.get(bld_no.upper())

    def drawing_source(self, bld_no: str) -> CatalogDrawingSource | None:
        return self.drawings.get(bld_no.upper())


def _seed(database_path: Path) -> None:
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO customers (id, name, sync_id) VALUES (1, '测试客户', ?)",
            (CUSTOMER_SYNC_ID,),
        )
        connection.execute(
            "INSERT INTO customers (id, name, sync_id) VALUES (2, '另一客户', ?)",
            ("b" * 64,),
        )
        connection.commit()


def _make_service(
    database_path: Path,
    storage_root: Path,
    *,
    quote_history: FakeQuoteHistory | None = None,
    catalog: FakeCatalog | None = None,
) -> CustomerProductService:
    return CustomerProductService(
        lambda: SQLiteCustomerProductUnitOfWork(database_path),
        CustomerDrawingFileStore(storage_root, max_file_bytes=1024 * 1024),
        quote_history or FakeQuoteHistory([QuotedProductOption(bld_no="K8053", customer_product_code="CUST-001")]),
        catalog if catalog is not None else FakeCatalog(),
    )


@pytest.fixture
def product_env(tmp_path: Path) -> tuple[CustomerProductService, Path, Path, FakeQuoteHistory]:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    quote_history = FakeQuoteHistory([QuotedProductOption(bld_no="K8053", customer_product_code="CUST-001")])
    service = _make_service(database_path, storage_root, quote_history=quote_history)
    return service, database_path, storage_root, quote_history


def _upload(name: str, payload: bytes, content_type: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(payload), filename=name, content_type=content_type)


def test_create_requires_quoted_bld_no_and_defaults_from_history(product_env) -> None:
    service, database_path, _storage_root, quote_history = product_env

    product = service.create(1, "k8053", actor="007")
    assert product.bld_no == "K8053"
    # code 缺省回填报价历史中的客户料号。
    assert product.customer_product_code == "CUST-001"
    assert quote_history.calls == [(1, "测试客户")]

    with pytest.raises(CustomerProductValidationError) as rejected:
        service.create(1, "NEVER-QUOTED", actor="007")
    assert rejected.value.code == "customer_product.bld_not_quoted"
    assert rejected.value.field == "bld_no"

    with pytest.raises(CustomerProductValidationError) as empty:
        service.create(1, "  ", actor="007")
    assert empty.value.code == "customer_product.bld_no_required"

    with pytest.raises(CustomerProductValidationError) as duplicate:
        service.create(1, "K8053", actor="007")
    assert duplicate.value.code == "customer_product.duplicate"

    with connect(database_path) as connection:
        actions = [row["action"] for row in connection.execute("SELECT action FROM audit_logs ORDER BY id")]
    assert actions == ["新增客户商品"]


def test_create_defaults_name_from_catalog(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    catalog = FakeCatalog(infos={"K8053": CatalogProductInfo(bld_no="K8053", item_name="支架总成")})
    service = _make_service(database_path, storage_root, catalog=catalog)

    product = service.create(1, "K8053", actor="007")
    assert product.customer_product_name == "支架总成"

    explicit = service.update(1, product.id, code="C-9", name="自定义名称", actor="007")
    assert explicit.customer_product_code == "C-9"
    assert explicit.customer_product_name == "自定义名称"
    # bld_no 不可通过 update 修改。
    assert explicit.bld_no == "K8053"


def test_create_with_customer_drawing_saves_v1_atomically(product_env) -> None:
    service, database_path, storage_root, _quote_history = product_env

    product = service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("客户来图.png", PNG_PAYLOAD, "image/png"),),
        customer_drawing_revision_label="Rev A",
    )

    slot = product.slot("customer")
    assert slot is not None
    assert slot.current_version == 1
    assert slot.current_file is not None
    assert slot.current_file.revision_label == "Rev A"
    assert slot.current_file.note == "新增客户商品时上传"
    payload = service.file_payload(1, slot.current_file.id, actor="007")
    assert payload.path.read_bytes() == PNG_PAYLOAD
    assert payload.path.is_relative_to(storage_root)

    with connect(database_path) as connection:
        actions = [row[0] for row in connection.execute("SELECT action FROM audit_logs ORDER BY id")]
    assert actions == ["新增客户商品", "上传客户图纸版本"]


def test_create_invalid_customer_drawing_rolls_back_product(product_env) -> None:
    service, database_path, storage_root, _quote_history = product_env

    with pytest.raises(CustomerProductValidationError) as rejected:
        service.create(
            1,
            "K8053",
            actor="007",
            customer_drawing_files=(_upload("伪造.pdf", b"not a pdf", "application/pdf"),),
        )
    assert rejected.value.code == "customer_drawing.invalid_file_content"

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_groups").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0
    assert not storage_root.exists() or not any(path.is_file() for path in storage_root.rglob("*"))


def test_list_for_customer_includes_slots_and_catalog(product_env) -> None:
    service, _database_path, _storage_root, _quote_history = product_env
    product = service.create(1, "K8053", actor="007")
    service.upload_version(
        1,
        product.id,
        "customer",
        (_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
        actor="007",
    )

    products = service.list_for_customer(1)
    assert len(products) == 1
    row = products[0]
    assert row.bld_no == "K8053"
    customer_slot = row.slot("customer")
    assert customer_slot is not None
    assert customer_slot.kind_label == "客户图纸"
    assert customer_slot.current_version == 1
    assert row.slot("bld") is None

    with pytest.raises(CustomerProductValidationError):
        service.list_for_customer(99)


def test_upload_version_auto_marks_latest_as_current(product_env) -> None:
    service, database_path, storage_root, _quote_history = product_env
    product = service.create(1, "K8053", actor="007")

    first = service.upload_version(
        1,
        product.id,
        "bld",
        (_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
        revision_label="Rev A",
        actor="007",
    )
    slot = first.slot("bld")
    assert slot is not None
    assert slot.kind_label == "BLD 图纸"
    assert slot.current_version == 1
    assert slot.current_file is not None
    assert slot.current_file.storage_path.startswith(f"{CUSTOMER_SYNC_ID}/drawings/{slot.sync_id}/v0001/")

    second = service.upload_version(
        1,
        product.id,
        "bld",
        (_upload("支架-v2.png", PNG_PAYLOAD + b"v2", "image/png"),),
        revision_label="Rev B",
        note="按客户 8 月意见修改",
        actor="007",
    )
    slot = second.slot("bld")
    assert slot is not None
    # 新版本自动成为当前版本。
    assert slot.current_version == 2
    assert [version.version_no for version in slot.versions] == [2, 1]
    current = slot.versions[0]
    assert current.revision_label == "Rev B"
    assert current.note == "按客户 8 月意见修改"
    assert current.file.uploaded_by == "007"

    payload = service.file_payload(1, current.file.id, actor="007", for_preview=True)
    assert payload.path.read_bytes() == PNG_PAYLOAD + b"v2"
    assert payload.content_type == "image/png"
    assert payload.previewable is True
    assert payload.path.is_relative_to(storage_root)

    history = service.version_history(1, product.id, "bld")
    assert history is not None
    assert [version.version_no for version in history.versions] == [2, 1]

    with connect(database_path) as connection:
        stored_paths = [row[0] for row in connection.execute("SELECT storage_path FROM customer_drawing_files")]
        actions = [row[0] for row in connection.execute("SELECT action FROM audit_logs ORDER BY id")]
    assert all(not Path(path).is_absolute() and ".." not in Path(path).parts for path in stored_paths)
    # GET preview/download stay read-only; only explicit mutations are audited.
    assert actions == ["新增客户商品", "上传客户图纸版本", "上传客户图纸版本"]
    assert not list(storage_root.rglob("*.tmp"))


def test_set_current_version_rolls_back_and_upload_continues_from_max(product_env) -> None:
    service, _database_path, _storage_root, _quote_history = product_env
    product = service.create(1, "K8053", actor="007")
    for index in range(1, 3):
        service.upload_version(
            1,
            product.id,
            "customer",
            (_upload(f"支架-v{index}.png", PNG_PAYLOAD + bytes([index]), "image/png"),),
            actor="007",
        )

    rolled_back = service.set_current_version(1, product.id, "customer", 1, actor="007")
    slot = rolled_back.slot("customer")
    assert slot is not None
    assert slot.current_version == 1
    assert slot.current_file is not None
    assert slot.current_file.version_no == 1

    # 回拨后再上传：版本号按历史最大版本 +1 继续递增，并自动成为当前。
    third = service.upload_version(
        1,
        product.id,
        "customer",
        (_upload("支架-v3.png", PNG_PAYLOAD + b"v3", "image/png"),),
        actor="007",
    )
    slot = third.slot("customer")
    assert slot is not None
    assert [version.version_no for version in slot.versions] == [3, 2, 1]
    assert slot.current_version == 3

    with pytest.raises(CustomerProductValidationError) as missing:
        service.set_current_version(1, product.id, "customer", 99, actor="007")
    assert missing.value.code == "customer_drawing.version_not_found"

    with pytest.raises(CustomerProductValidationError) as empty_slot:
        service.set_current_version(1, product.id, "bld", 1, actor="007")
    assert empty_slot.value.code == "customer_drawing.slot_empty"


def test_cross_customer_access_is_rejected(product_env) -> None:
    service, _database_path, _storage_root, _quote_history = product_env
    product = service.create(1, "K8053", actor="007")
    updated = service.upload_version(
        1,
        product.id,
        "customer",
        (_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
        actor="007",
    )
    slot = updated.slot("customer")
    assert slot is not None and slot.current_file is not None

    # 用 customer 2 的身份操作 customer 1 的资源，一律按 not_found 拒绝。
    with pytest.raises(CustomerProductValidationError) as file_error:
        service.file_payload(2, slot.current_file.id, actor="007")
    assert file_error.value.code == "customer_drawing.file_not_found"

    for action in (
        lambda: service.get(2, product.id),
        lambda: service.update(2, product.id, code="C-X", name="越权改名", actor="007"),
        lambda: service.delete(2, product.id, actor="007"),
        lambda: service.upload_version(
            2,
            product.id,
            "customer",
            (_upload("越权-v2.png", PNG_PAYLOAD, "image/png"),),
            actor="007",
        ),
        lambda: service.set_current_version(2, product.id, "customer", 1, actor="007"),
        lambda: service.version_history(2, product.id, "customer"),
    ):
        with pytest.raises(CustomerProductValidationError) as rejected:
            action()
        assert rejected.value.code == "customer_product.not_found"


def test_set_current_version_rejects_overlong_version_text(product_env) -> None:
    service, _database_path, _storage_root, _quote_history = product_env
    product = service.create(1, "K8053", actor="007")
    service.upload_version(
        1,
        product.id,
        "customer",
        (_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
        actor="007",
    )

    # 超长数字串超过 int() 位数上限，必须走 400 验证错误而非 500。
    with pytest.raises(CustomerProductValidationError) as rejected:
        service.set_current_version(1, product.id, "customer", "9" * 5000, actor="007")
    assert rejected.value.code == "customer_drawing.invalid_version"
    assert rejected.value.field == "version_no"


def test_import_catalog_drawing_copies_into_bld_slot(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    catalog_pdf = tmp_path / "catalog" / "K8053.pdf"
    catalog_pdf.parent.mkdir(parents=True)
    catalog_pdf.write_bytes(PDF_PAYLOAD)
    catalog = FakeCatalog(drawings={"K8053": CatalogDrawingSource(path=catalog_pdf, original_name="K8053-目录图纸.pdf")})
    service = _make_service(database_path, storage_root, catalog=catalog)

    product = service.create(1, "K8053", actor="007")
    updated = service.import_catalog_drawing(1, product.id, actor="007")
    slot = updated.slot("bld")
    assert slot is not None
    assert slot.current_version == 1
    assert slot.current_file is not None
    assert slot.current_file.original_name == "K8053-目录图纸.pdf"
    assert slot.current_file.note == "引入自产品目录图纸"

    payload = service.file_payload(1, slot.current_file.id, actor="007")
    assert payload.path.read_bytes() == PDF_PAYLOAD
    assert payload.path.is_relative_to(storage_root)
    # 目录原文件保持不动。
    assert catalog_pdf.read_bytes() == PDF_PAYLOAD

    with connect(database_path) as connection:
        actions = [row[0] for row in connection.execute("SELECT action FROM audit_logs ORDER BY id")]
    assert actions == ["新增客户商品", "引入产品目录图纸"]


def test_import_catalog_drawing_rejects_when_catalog_has_no_drawing(product_env) -> None:
    service, _database_path, _storage_root, _quote_history = product_env
    product = service.create(1, "K8053", actor="007")

    with pytest.raises(CustomerProductValidationError) as missing:
        service.import_catalog_drawing(1, product.id, actor="007")
    assert missing.value.code == "customer_product.catalog_drawing_missing"
    assert service.version_history(1, product.id, "bld") is None


def test_upload_validation_rejects_extension_signature_size_and_unsafe_paths(product_env) -> None:
    service, database_path, storage_root, _quote_history = product_env
    product = service.create(1, "K8053", actor="007")

    with pytest.raises(CustomerProductValidationError) as extension_error:
        service.upload_version(
            1,
            product.id,
            "bld",
            (_upload("notes.txt", b"plain text", "text/plain"),),
            actor="007",
        )
    assert extension_error.value.code == "customer_drawing.extension_not_allowed"

    with pytest.raises(CustomerProductValidationError) as signature_error:
        service.upload_version(
            1,
            product.id,
            "bld",
            (_upload("not-a-pdf.pdf", b"plain text", "application/pdf"),),
            actor="007",
        )
    assert signature_error.value.code == "customer_drawing.invalid_file_content"

    with pytest.raises(CustomerProductValidationError) as kind_error:
        service.upload_version(
            1,
            product.id,
            "sideways",
            (_upload("ok.png", PNG_PAYLOAD, "image/png"),),
            actor="007",
        )
    assert kind_error.value.code == "customer_drawing.invalid_kind"

    tiny_store = CustomerDrawingFileStore(storage_root, max_file_bytes=4)
    with pytest.raises(CustomerProductValidationError) as size_error:
        tiny_store.prepare(
            (_upload("large.png", PNG_PAYLOAD, "image/png"),),
            customer_sync_id=CUSTOMER_SYNC_ID,
            group_sync_id="b" * 32,
            version_no=1,
        )
    assert size_error.value.code == "customer_drawing.file_too_large"

    with pytest.raises(CustomerProductValidationError) as path_error:
        tiny_store.resolve(f"{CUSTOMER_SYNC_ID}/drawings/../../secret.png", customer_sync_id=CUSTOMER_SYNC_ID)
    assert path_error.value.code == "customer_drawing.unsafe_path"

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_groups").fetchone()[0] == 0


def test_database_commit_failure_compensates_promoted_files(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)

    class FailingCommitUnitOfWork(SQLiteCustomerProductUnitOfWork):
        def commit(self) -> None:
            raise sqlite3.OperationalError("forced commit failure")

    product = _make_service(database_path, storage_root).create(1, "K8053", actor="007")
    service = CustomerProductService(
        lambda: FailingCommitUnitOfWork(database_path),
        CustomerDrawingFileStore(storage_root),
        FakeQuoteHistory([QuotedProductOption(bld_no="K8053")]),
        FakeCatalog(),
    )
    with pytest.raises(sqlite3.OperationalError, match="forced commit failure"):
        service.upload_version(
            1,
            product.id,
            "bld",
            (_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
            actor="007",
        )

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM customer_drawing_groups WHERE customer_product_id = ?",
                (product.id,),
            ).fetchone()[0]
            == 0
        )
    assert not storage_root.exists() or not any(storage_root.rglob("*"))


def test_create_commit_failure_compensates_product_and_initial_drawing(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)

    class FailingCommitUnitOfWork(SQLiteCustomerProductUnitOfWork):
        def commit(self) -> None:
            raise sqlite3.OperationalError("forced create commit failure")

    service = CustomerProductService(
        lambda: FailingCommitUnitOfWork(database_path),
        CustomerDrawingFileStore(storage_root),
        FakeQuoteHistory([QuotedProductOption(bld_no="K8053")]),
        FakeCatalog(),
    )
    with pytest.raises(sqlite3.OperationalError, match="forced create commit failure"):
        service.create(
            1,
            "K8053",
            actor="007",
            customer_drawing_files=(_upload("客户来图.png", PNG_PAYLOAD, "image/png"),),
        )

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_groups").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0
    assert not storage_root.exists() or not any(path.is_file() for path in storage_root.rglob("*"))


def test_create_successful_commit_keeps_initial_drawing_when_uow_exit_fails(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)

    class FailingExitAfterCommitUnitOfWork(SQLiteCustomerProductUnitOfWork):
        def __exit__(self, exc_type, exc, traceback) -> None:
            super().__exit__(exc_type, exc, traceback)
            if exc_type is None:
                raise RuntimeError("forced close failure after create commit")

    service = CustomerProductService(
        lambda: FailingExitAfterCommitUnitOfWork(database_path),
        CustomerDrawingFileStore(storage_root),
        FakeQuoteHistory([QuotedProductOption(bld_no="K8053")]),
        FakeCatalog(),
    )

    with pytest.raises(RuntimeError, match="forced close failure after create commit"):
        service.create(
            1,
            "K8053",
            actor="007",
            customer_drawing_files=(_upload("客户来图.png", PNG_PAYLOAD, "image/png"),),
        )

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products").fetchone()[0] == 1
        file_row = connection.execute("SELECT storage_path FROM customer_drawing_files").fetchone()
        assert file_row is not None
    assert (storage_root / file_row["storage_path"]).read_bytes() == PNG_PAYLOAD


def test_upload_successful_commit_keeps_file_when_uow_exit_fails(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    product = _make_service(database_path, storage_root).create(1, "K8053", actor="007")

    class FailingExitAfterCommitUnitOfWork(SQLiteCustomerProductUnitOfWork):
        def __exit__(self, exc_type, exc, traceback) -> None:
            super().__exit__(exc_type, exc, traceback)
            if exc_type is None:
                raise RuntimeError("forced close failure after upload commit")

    service = CustomerProductService(
        lambda: FailingExitAfterCommitUnitOfWork(database_path),
        CustomerDrawingFileStore(storage_root),
        FakeQuoteHistory([QuotedProductOption(bld_no="K8053")]),
        FakeCatalog(),
    )

    with pytest.raises(RuntimeError, match="forced close failure after upload commit"):
        service.upload_version(
            1,
            product.id,
            "bld",
            (_upload("BLD-v1.pdf", PDF_PAYLOAD, "application/pdf"),),
            actor="007",
        )

    with connect(database_path) as connection:
        file_row = connection.execute("SELECT storage_path FROM customer_drawing_files").fetchone()
        assert file_row is not None
    assert (storage_root / file_row["storage_path"]).read_bytes() == PDF_PAYLOAD


def test_version_claim_conflict_discards_staged_upload(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)

    class ConflictingClaimUnitOfWork(SQLiteCustomerProductUnitOfWork):
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
                    new_version: int,
                    actor: str,
                ) -> bool:
                    return False

            unit_of_work.repository = ConflictingClaimRepository(unit_of_work.connection)
            return unit_of_work

    service = CustomerProductService(
        lambda: ConflictingClaimUnitOfWork(database_path),
        CustomerDrawingFileStore(storage_root, max_file_bytes=1024 * 1024),
        FakeQuoteHistory([QuotedProductOption(bld_no="K8053")]),
        FakeCatalog(),
    )
    product = service.create(1, "K8053", actor="007")

    with pytest.raises(CustomerProductValidationError) as conflict_error:
        service.upload_version(
            1,
            product.id,
            "bld",
            (_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
            actor="007",
        )
    assert conflict_error.value.code == "customer_drawing.version_conflict"

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0
        slot_row = connection.execute(
            "SELECT current_version FROM customer_drawing_groups WHERE customer_product_id = ?",
            (product.id,),
        ).fetchone()
    # 版本占位失败整体回滚：图纸位未落库，暂存文件被 discard，磁盘无残留。
    assert slot_row is None
    files_on_disk = [path for path in storage_root.rglob("*") if path.is_file()]
    assert files_on_disk == []


def test_file_payload_rejects_tampered_storage_content(product_env) -> None:
    service, _database_path, storage_root, _quote_history = product_env
    product = service.create(1, "K8053", actor="007")
    updated = service.upload_version(
        1,
        product.id,
        "customer",
        (_upload("支架-v1.png", PNG_PAYLOAD, "image/png"),),
        actor="007",
    )
    slot = updated.slot("customer")
    assert slot is not None and slot.current_file is not None

    stored = next(path for path in storage_root.rglob("*.png") if path.is_file())
    stored.write_bytes(b"tampered-content")

    with pytest.raises(CustomerProductValidationError) as corrupt_error:
        service.file_payload(1, slot.current_file.id, actor="007")
    assert corrupt_error.value.code == "customer_drawing.file_corrupt"


def test_delete_product_removes_all_drawings_and_quote_links(product_env) -> None:
    service, database_path, storage_root, _quote_history = product_env
    product = service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("客户-v1.png", PNG_PAYLOAD, "image/png"),),
    )
    product = service.upload_version(
        1,
        product.id,
        "customer",
        (_upload("客户-v2.png", PNG_PAYLOAD + b"v2", "image/png"),),
        actor="007",
    )
    product = service.upload_version(
        1,
        product.id,
        "bld",
        (_upload("BLD-v1.pdf", PDF_PAYLOAD, "application/pdf"),),
        actor="007",
    )
    file_ids = [file.id for slot in product.drawings for file in slot.files]
    stored_files = [path for path in storage_root.rglob("*") if path.is_file()]
    assert len(stored_files) == 3

    with connect(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO quote_records
              (customer_id, customer_name, product_model, currency, quote_date, created_at, updated_at)
            VALUES (1, '测试客户', 'K8053', 'USD', '2026-08-07', '2026-08-07', '2026-08-07')
            """
        )
        quote_id = int(cursor.lastrowid)
        connection.executemany(
            "INSERT INTO quote_record_drawings (quote_record_id, drawing_file_id, created_at) VALUES (?, ?, '2026-08-07')",
            [(quote_id, file_id) for file_id in file_ids],
        )
        connection.commit()

    deletion = service.delete(1, product.id, actor="007")
    deleted = deletion.product
    assert deleted.bld_no == "K8053"
    assert sum(len(slot.files) for slot in deleted.drawings) == 3
    assert deletion.drawing_file_count == 3
    assert deletion.cleanup_complete

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_groups").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM quote_record_drawings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM quote_records WHERE id = ?", (quote_id,)).fetchone()[0] == 1
        actions = [row[0] for row in connection.execute("SELECT action FROM audit_logs ORDER BY id")]
    assert actions[-1] == "删除客户商品"
    assert not any(path.is_file() for path in storage_root.rglob("*"))

    recreated = service.create(1, "K8053", actor="007")
    assert recreated.id != product.id


def test_delete_product_without_drawings_removes_product(product_env) -> None:
    service, database_path, storage_root, _quote_history = product_env
    product = service.create(1, "K8053", actor="007")

    deletion = service.delete(1, product.id, actor="007")
    deleted = deletion.product

    assert deleted.id == product.id
    assert deleted.drawings == ()
    assert deletion.drawing_file_count == 0
    assert deletion.cleanup_complete
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_logs WHERE action = '删除客户商品'").fetchone()[0] == 1
    assert not storage_root.exists()


def test_delete_rejects_path_that_crosses_into_another_live_drawing_slot(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    service = _make_service(
        database_path,
        storage_root,
        quote_history=FakeQuoteHistory(
            [
                QuotedProductOption(bld_no="K8053"),
                QuotedProductOption(bld_no="K9000"),
            ]
        ),
    )
    first = service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("first.png", PNG_PAYLOAD, "image/png"),),
    )
    second = service.create(
        1,
        "K9000",
        actor="007",
        customer_drawing_files=(_upload("second.png", PNG_PAYLOAD + b"second", "image/png"),),
    )
    first_slot = first.slot("customer")
    second_slot = second.slot("customer")
    assert first_slot is not None and first_slot.current_file is not None
    assert second_slot is not None and second_slot.current_file is not None
    first_original = storage_root / first_slot.current_file.storage_path
    second_original = storage_root / second_slot.current_file.storage_path
    unrelated_relative = PurePosixPath(
        CUSTOMER_SYNC_ID,
        "drawings",
        second_slot.sync_id,
        "v9999",
        "unrelated.png",
    ).as_posix()
    unrelated_file = storage_root / unrelated_relative
    unrelated_file.parent.mkdir(parents=True, exist_ok=True)
    unrelated_file.write_bytes(b"unrelated-customer-file")
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE customer_drawing_files SET storage_path = ? WHERE id = ?",
            (unrelated_relative, first_slot.current_file.id),
        )
        connection.commit()

    with pytest.raises(CustomerProductValidationError) as unsafe:
        service.delete(1, first.id, actor="007")

    assert unsafe.value.code == "customer_drawing.unsafe_path"
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM audit_logs WHERE action = '删除客户商品'").fetchone()[0] == 0
    assert first_original.read_bytes() == PNG_PAYLOAD
    assert second_original.read_bytes() == PNG_PAYLOAD + b"second"
    assert unrelated_file.read_bytes() == b"unrelated-customer-file"
    assert not (storage_root / ".deleting").exists()


def test_delete_rejects_drawing_slot_with_mismatched_customer(product_env) -> None:
    service, database_path, storage_root, _quote_history = product_env
    product = service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("owned.png", PNG_PAYLOAD, "image/png"),),
    )
    slot = product.slot("customer")
    assert slot is not None and slot.current_file is not None
    stored_path = storage_root / slot.current_file.storage_path
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE customer_drawing_groups SET customer_id = 2 WHERE id = ?",
            (slot.id,),
        )
        connection.commit()

    with pytest.raises(CustomerProductValidationError) as mismatch:
        service.delete(1, product.id, actor="007")

    assert mismatch.value.code == "customer_drawing.ownership_mismatch"
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products WHERE id = ?", (product.id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_groups WHERE id = ?", (slot.id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_logs WHERE action = '删除客户商品'").fetchone()[0] == 0
    assert stored_path.read_bytes() == PNG_PAYLOAD
    assert not (storage_root / ".deleting").exists()


def test_delete_accepts_legacy_merged_group_directory(product_env) -> None:
    service, database_path, storage_root, _quote_history = product_env
    product = service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("legacy.png", PNG_PAYLOAD, "image/png"),),
    )
    slot = product.slot("customer")
    assert slot is not None and slot.current_file is not None
    original_path = storage_root / slot.current_file.storage_path
    original_parts = PurePosixPath(slot.current_file.storage_path).parts
    legacy_relative = PurePosixPath(
        CUSTOMER_SYNC_ID,
        "drawings",
        "legacy-group-01",
        *original_parts[3:],
    ).as_posix()
    legacy_path = storage_root / legacy_relative
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.replace(legacy_path)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE customer_drawing_files SET storage_path = ? WHERE id = ?",
            (legacy_relative, slot.current_file.id),
        )
        connection.commit()

    service.delete(1, product.id, actor="007")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0
    assert not legacy_path.exists()
    assert not (storage_root / ".deleting").exists()


def test_delete_rejects_symlink_that_escapes_drawing_slot(product_env) -> None:
    service, database_path, storage_root, _quote_history = product_env
    product = service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("linked.png", PNG_PAYLOAD, "image/png"),),
    )
    slot = product.slot("customer")
    assert slot is not None and slot.current_file is not None
    stored_path = storage_root / slot.current_file.storage_path
    unrelated_file = storage_root / "other-customer" / "documents" / "keep.txt"
    unrelated_file.parent.mkdir(parents=True, exist_ok=True)
    unrelated_file.write_bytes(b"must-not-be-deleted")
    stored_path.unlink()
    try:
        stored_path.symlink_to(unrelated_file)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(CustomerProductValidationError) as unsafe:
        service.delete(1, product.id, actor="007")

    assert unsafe.value.code == "customer_drawing.unsafe_path"
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products WHERE id = ?", (product.id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_logs WHERE action = '删除客户商品'").fetchone()[0] == 0
    assert unrelated_file.read_bytes() == b"must-not-be-deleted"
    assert stored_path.is_symlink()
    assert not (storage_root / ".deleting").exists()


def test_delete_staging_failure_with_incomplete_restore_is_explicit_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    base_service = _make_service(database_path, storage_root)
    product = base_service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("客户-v1.png", PNG_PAYLOAD, "image/png"),),
    )
    product = base_service.upload_version(
        1,
        product.id,
        "bld",
        (_upload("BLD-v1.pdf", PDF_PAYLOAD, "application/pdf"),),
        actor="007",
    )

    class StagingAndRestoreFailureStore(CustomerDrawingFileStore):
        destination_calls = 0
        conflict_path: Path | None = None
        failures: tuple[CustomerFileRemovalFailure, ...] = ()

        def _destination(
            self,
            storage_path: str,
            *,
            customer_sync_id: str,
            group_sync_id: str = "",
        ) -> Path:
            self.destination_calls += 1
            if self.destination_calls == 2:
                raise CustomerProductValidationError(
                    "customer_drawing.forced_stage_failure", "forced second-file staging failure"
                )
            return super()._destination(
                storage_path,
                customer_sync_id=customer_sync_id,
                group_sync_id=group_sync_id,
            )

        def restore_removal(
            self, batch: StagedCustomerFileRemovalBatch
        ) -> tuple[CustomerFileRemovalFailure, ...]:
            if batch.files and self.conflict_path is None:
                self.conflict_path = batch.files[-1].original_path
                self.conflict_path.parent.mkdir(parents=True, exist_ok=True)
                self.conflict_path.write_bytes(b"conflicting replacement")
            self.failures = super().restore_removal(batch)
            return self.failures

    storage = StagingAndRestoreFailureStore(storage_root)
    service = CustomerProductService(
        lambda: SQLiteCustomerProductUnitOfWork(database_path),
        storage,
        FakeQuoteHistory([QuotedProductOption(bld_no="K8053")]),
        FakeCatalog(),
    )

    with pytest.raises(CustomerProductValidationError) as caught:
        service.delete(1, product.id, actor="007")

    assert caught.value.code == "customer_drawing.restore_incomplete"
    assert "恢复不完整" in caught.value.message
    assert "联系管理员" in caught.value.message
    assert isinstance(caught.value.__cause__, CustomerProductValidationError)
    assert caught.value.__cause__.code == "customer_drawing.forced_stage_failure"
    assert len(storage.failures) == 1
    failure = storage.failures[0]
    assert failure.staged_path.exists()
    log_record = next(
        record
        for record in caplog.records
        if record.message == "Customer drawing staging failed and staged file restore was incomplete"
    )
    assert log_record.original_path == str(failure.original_path)
    assert log_record.staged_path == str(failure.staged_path)
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products WHERE id = ?", (product.id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM audit_logs WHERE action = '删除客户商品'").fetchone()[0] == 0


def test_delete_commit_failure_restores_rows_and_files(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    base_service = _make_service(database_path, storage_root)
    product = base_service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("客户-v1.png", PNG_PAYLOAD, "image/png"),),
    )
    original_file = next(path for path in storage_root.rglob("*.png") if path.is_file())

    class FailingDeleteCommitUnitOfWork(SQLiteCustomerProductUnitOfWork):
        def commit(self) -> None:
            raise sqlite3.OperationalError("forced delete commit failure")

    service = CustomerProductService(
        lambda: FailingDeleteCommitUnitOfWork(database_path),
        CustomerDrawingFileStore(storage_root),
        FakeQuoteHistory([QuotedProductOption(bld_no="K8053")]),
        FakeCatalog(),
    )
    with pytest.raises(sqlite3.OperationalError, match="forced delete commit failure"):
        service.delete(1, product.id, actor="007")

    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products WHERE id = ?", (product.id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_groups").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_logs WHERE action = '删除客户商品'").fetchone()[0] == 0
    assert original_file.read_bytes() == PNG_PAYLOAD
    assert not (storage_root / ".deleting").exists()


def test_delete_successful_commit_still_finalizes_files_when_uow_exit_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    base_service = _make_service(database_path, storage_root)
    product = base_service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("客户-v1.png", PNG_PAYLOAD, "image/png"),),
    )
    original_file = next(path for path in storage_root.rglob("*.png") if path.is_file())

    class FailingExitAfterDeleteCommitUnitOfWork(SQLiteCustomerProductUnitOfWork):
        def __exit__(self, exc_type, exc, traceback) -> None:
            super().__exit__(exc_type, exc, traceback)
            if exc_type is None:
                raise RuntimeError("forced close failure after delete commit")

    service = CustomerProductService(
        lambda: FailingExitAfterDeleteCommitUnitOfWork(database_path),
        CustomerDrawingFileStore(storage_root),
        FakeQuoteHistory([QuotedProductOption(bld_no="K8053")]),
        FakeCatalog(),
    )

    deletion = service.delete(1, product.id, actor="007")

    assert deletion.product.id == product.id
    assert deletion.post_commit_warning
    assert deletion.cleanup_complete
    assert not original_file.exists()
    assert not (storage_root / ".deleting").exists()
    assert "unit-of-work exit failed; continuing cleanup" in caplog.text
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0


def test_delete_rollback_restores_remaining_files_after_first_restore_conflict(tmp_path: Path) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    base_service = _make_service(database_path, storage_root)
    product = base_service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("客户-v1.png", PNG_PAYLOAD, "image/png"),),
    )
    product = base_service.upload_version(
        1,
        product.id,
        "customer",
        (_upload("客户-v2.png", PNG_PAYLOAD + b"v2", "image/png"),),
        actor="007",
    )
    product = base_service.upload_version(
        1,
        product.id,
        "bld",
        (_upload("BLD-v1.pdf", PDF_PAYLOAD, "application/pdf"),),
        actor="007",
    )
    original_payloads = {
        storage_root / file.storage_path: (storage_root / file.storage_path).read_bytes()
        for slot in product.drawings
        for file in slot.files
    }

    class FailingDeleteCommitUnitOfWork(SQLiteCustomerProductUnitOfWork):
        def commit(self) -> None:
            raise sqlite3.OperationalError("forced delete commit failure with restore conflict")

    class RestoreConflictStore(CustomerDrawingFileStore):
        conflict_path: Path | None = None
        failures: tuple[CustomerFileRemovalFailure, ...] = ()

        def restore_removal(
            self, batch: StagedCustomerFileRemovalBatch
        ) -> tuple[CustomerFileRemovalFailure, ...]:
            if batch.files and self.conflict_path is None:
                # restore_removal iterates in reverse, so this conflict is encountered first.
                self.conflict_path = batch.files[-1].original_path
                self.conflict_path.parent.mkdir(parents=True, exist_ok=True)
                self.conflict_path.write_bytes(b"conflicting replacement")
            self.failures = super().restore_removal(batch)
            return self.failures

    storage = RestoreConflictStore(storage_root)
    service = CustomerProductService(
        lambda: FailingDeleteCommitUnitOfWork(database_path),
        storage,
        FakeQuoteHistory([QuotedProductOption(bld_no="K8053")]),
        FakeCatalog(),
    )

    with pytest.raises(sqlite3.OperationalError, match="forced delete commit failure") as caught:
        service.delete(1, product.id, actor="007")

    assert storage.conflict_path is not None
    assert len(storage.failures) == 1
    assert any("图纸文件恢复不完整" in note for note in getattr(caught.value, "__notes__", ()))
    for original_path, payload in original_payloads.items():
        if original_path == storage.conflict_path:
            assert original_path.read_bytes() == b"conflicting replacement"
        else:
            assert original_path.read_bytes() == payload
    assert storage.failures[0].staged_path.exists()
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products WHERE id = ?", (product.id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 3


def test_delete_purge_continues_after_one_file_fails_and_reports_partial_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "customer-products.sqlite3"
    storage_root = tmp_path / "data" / "customer_files"
    _seed(database_path)
    service = _make_service(database_path, storage_root)
    product = service.create(
        1,
        "K8053",
        actor="007",
        customer_drawing_files=(_upload("客户-v1.png", PNG_PAYLOAD, "image/png"),),
    )
    product = service.upload_version(
        1,
        product.id,
        "customer",
        (_upload("客户-v2.png", PNG_PAYLOAD + b"v2", "image/png"),),
        actor="007",
    )
    product = service.upload_version(
        1,
        product.id,
        "bld",
        (_upload("BLD-v1.pdf", PDF_PAYLOAD, "application/pdf"),),
        actor="007",
    )
    original_paths = [storage_root / file.storage_path for slot in product.drawings for file in slot.files]
    failed_staged_paths: list[Path] = []
    real_unlink = Path.unlink

    def fail_first_staged_unlink(path: Path, *args, **kwargs) -> None:
        if ".deleting" in path.parts and not failed_staged_paths:
            failed_staged_paths.append(path)
            raise PermissionError("forced staged purge failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_staged_unlink)

    deletion = service.delete(1, product.id, actor="007")

    assert deletion.drawing_file_count == 3
    assert not deletion.cleanup_complete
    assert deletion.cleanup_failure_count == 1
    assert len(failed_staged_paths) == 1
    assert failed_staged_paths[0].exists()
    assert all(not path.exists() for path in original_paths)
    remaining_files = [path for path in (storage_root / ".deleting").rglob("*") if path.is_file()]
    assert remaining_files == failed_staged_paths
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM customer_products").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM customer_drawing_files").fetchone()[0] == 0


def _legacy_035_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE customers (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          sync_id TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE quote_records (
          id INTEGER PRIMARY KEY,
          customer_id INTEGER,
          customer_name TEXT NOT NULL,
          bld_no TEXT DEFAULT '',
          product_model TEXT NOT NULL DEFAULT ''
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
        CREATE INDEX idx_customer_drawing_groups_customer ON customer_drawing_groups(customer_id, archived, updated_at);
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
        CREATE TABLE quote_record_drawings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          quote_record_id INTEGER NOT NULL REFERENCES quote_records(id),
          drawing_file_id INTEGER NOT NULL REFERENCES customer_drawing_files(id),
          created_by TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          UNIQUE(quote_record_id, drawing_file_id)
        );
        """
    )


def test_migration_036_rebuilds_groups_and_backfills_products(tmp_path: Path) -> None:
    from app.migrations import _rebuild_customer_drawing_groups

    database_path = tmp_path / "migration-036.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        _legacy_035_schema(connection)
        connection.execute("INSERT INTO customers (id, name, sync_id) VALUES (1, '测试客户', 'c-sync')")
        connection.execute("INSERT INTO quote_records (id, customer_name) VALUES (1, '测试客户')")
        connection.executemany(
            """
            INSERT INTO customer_drawing_groups
              (id, customer_id, sync_id, bld_no, direction, title, current_version, archived,
               created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '035', '2026-08-01', '2026-08-01')
            """,
            [
                (1, 1, "g-sync-1", "K8053", "customer", "支架图纸", 2, 0),
                (2, 1, "g-sync-2", "k8053", "issued", "我方图纸", 1, 0),
                # 与组 1 归并到同一商品同一图纸位：走文件并入路径。
                (3, 1, "g-sync-3", "K8053", "customer", "重复图纸", 1, 1),
                (4, 1, "g-sync-4", "", "customer", "无 BLD 图纸", 0, 0),
            ],
        )
        connection.executemany(
            """
            INSERT INTO customer_drawing_files
              (id, group_id, sync_id, version_no, original_name, storage_path, content_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'application/pdf', '2026-08-01')
            """,
            [
                (11, 1, "f-11", 1, "a-v1.pdf", "c-sync/drawings/g-sync-1/v0001/a.pdf"),
                (12, 1, "f-12", 2, "a-v2.pdf", "c-sync/drawings/g-sync-1/v0002/b.pdf"),
                (13, 2, "f-13", 1, "b-v1.pdf", "c-sync/drawings/g-sync-2/v0001/c.pdf"),
                (14, 3, "f-14", 1, "dup-v1.pdf", "c-sync/drawings/g-sync-3/v0001/d.pdf"),
            ],
        )
        connection.execute(
            "INSERT INTO quote_record_drawings (quote_record_id, drawing_file_id, created_at) VALUES (1, 11, '2026-08-01')"
        )

        _rebuild_customer_drawing_groups(connection)
        # 幂等：重复执行不报错且不产生重复数据。
        _rebuild_customer_drawing_groups(connection)

        products = connection.execute(
            "SELECT id, customer_id, bld_no FROM customer_products ORDER BY id"
        ).fetchall()
        assert [(row["customer_id"], row["bld_no"]) for row in products] == [(1, "K8053"), (1, "")]
        k8053_product_id = products[0]["id"]

        groups = connection.execute(
            "SELECT * FROM customer_drawing_groups ORDER BY id"
        ).fetchall()
        # 旧组 3 被并入组 1；组 1/2/4 保留原 id 与 sync_id，磁盘路径不受影响。
        assert [(row["id"], row["customer_product_id"], row["kind"], row["sync_id"]) for row in groups] == [
            (1, k8053_product_id, "customer", "g-sync-1"),
            (2, k8053_product_id, "bld", "g-sync-2"),
            (4, products[1]["id"], "customer", "g-sync-4"),
        ]
        # 并入后组 1 的当前版本顺移到最大版本号。
        group_one = groups[0]
        assert group_one["current_version"] == 3

        files = connection.execute(
            "SELECT id, group_id, version_no FROM customer_drawing_files ORDER BY id"
        ).fetchall()
        assert [(row["id"], row["group_id"], row["version_no"]) for row in files] == [
            (11, 1, 1),
            (12, 1, 2),
            (13, 2, 1),
            (14, 1, 3),
        ]
        # quote_record_drawings 与 customer_drawing_files 的关联不变。
        link = connection.execute("SELECT drawing_file_id FROM quote_record_drawings").fetchone()
        assert link["drawing_file_id"] == 11

        table_names = {
            row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "customer_drawing_groups_legacy" not in table_names

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO customer_drawing_groups
                  (customer_product_id, customer_id, sync_id, kind, created_at, updated_at)
                VALUES (?, 1, 'g-sync-x', 'sideways', '2026-08-03', '2026-08-03')
                """,
                (k8053_product_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO customer_drawing_groups
                  (customer_product_id, customer_id, sync_id, kind, created_at, updated_at)
                VALUES (?, 1, 'g-sync-y', 'bld', '2026-08-03', '2026-08-03')
                """,
                (k8053_product_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO customer_products (customer_id, sync_id, bld_no, created_at, updated_at)
                VALUES (1, 'p-sync-dup', 'k8053', '2026-08-03', '2026-08-03')
                """
            )
        indexes = {
            row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        assert {
            "idx_customer_products_customer_bld",
            "idx_customer_drawing_groups_product_kind",
            "idx_customer_drawing_groups_customer",
        }.issubset(indexes)
    finally:
        connection.close()


def test_migration_036_is_noop_on_fresh_schema(tmp_path: Path) -> None:
    from app.migrations import _rebuild_customer_drawing_groups

    database_path = tmp_path / "fresh.sqlite3"
    with connect(database_path) as connection:
        # 主 schema 已是新结构：迁移直接跳过重建。
        _rebuild_customer_drawing_groups(connection)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(customer_drawing_groups)")}
        assert {"customer_product_id", "kind", "current_version"}.issubset(columns)
        assert "direction" not in columns
        product_columns = {row["name"] for row in connection.execute("PRAGMA table_info(customer_products)")}
        assert {"customer_id", "bld_no", "customer_product_code", "customer_product_name"}.issubset(product_columns)
