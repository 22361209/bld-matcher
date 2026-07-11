from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from werkzeug.datastructures import FileStorage, MultiDict

from app.database import connect
from app.modules.products.persistence import upsert_product
from app.modules.admin.repository import SQLiteAdminUnitOfWork
from app.modules.admin.service import AdminService
from app.modules.contracts.repository import SQLiteContractRepository, SQLiteContractUnitOfWork
from app.modules.contracts.service import ContractService
from app.modules.materials.infrastructure import MaterialFileAdapter
from app.modules.materials.repository import SQLiteMaterialRepository, SQLiteMaterialUnitOfWork
from app.modules.materials.service import MaterialService
from app.modules.products.repository import SQLiteProductUnitOfWork
from app.modules.products.service import ProductService
from app.modules.shipping.infrastructure import ShippingTemplateStore, ShippingWorkbookAdapter
from app.modules.shipping.repository import SQLiteShippingUnitOfWork
from app.modules.shipping.service import ShippingNoticeService


class _UpdateReader:
    source_name = "test-updates"

    def read(self) -> list[dict[str, object]]:
        return [{"date": "2026-07-11", "title": "Test", "entries": ["ready"]}]


class _PdfAdapter:
    def __init__(self) -> None:
        self.contract: dict | None = None

    def generate(self, kind: str, contract: dict, output_path: Path) -> None:
        self.contract = contract
        output_path.write_bytes(f"%PDF-test-{kind}".encode())


@contextmanager
def _no_lock(_actor: str, _label: str):
    yield


def _material_book(path: Path, *, model: str, code: str) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "材料数据"
    sheet.append(["型号", "编码", "类别", "车型", "零件名称", "规格尺寸", "下料只数", "单重", "厚度", "宽度", "长度"])
    sheet.append([model, code, "测试", "测试车型", "测试零件", "旧规格", 2, "", 2.5, 100, 200])
    workbook.save(path)
    workbook.close()


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


class DomainPageModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "data" / "products.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_admin_service_owns_user_key_and_log_transactions(self) -> None:
        service = AdminService(
            lambda: SQLiteAdminUnitOfWork(self.database_path),
            _UpdateReader(),
            lambda _stored, _password: False,
        )
        service.save_user(
            {
                "username": "service-user",
                "display_name": "Service User",
                "role": "editor",
                "active": "1",
                "password": "secret-123",
            },
            actor="module-test",
        )
        users, editing = service.users()
        self.assertIsNone(editing)
        self.assertEqual([row["username"] for row in users], ["service-user"])

        token, page = service.create_api_key(
            actor="module-test",
            name="service-key",
            scopes=["products:read"],
            expires_at="",
        )
        self.assertTrue(token.startswith("bld_sk_"))
        self.assertEqual(page.keys[0]["scopes"], ["products:read"])
        logs, actors = service.logs(actor="module-test")
        self.assertGreaterEqual(len(logs), 2)
        self.assertIn("module-test", actors)
        updates, source = service.system_updates()
        self.assertEqual(updates[0]["title"], "Test")
        self.assertEqual(source, "test-updates")

    def test_material_import_restores_file_when_database_transaction_fails(self) -> None:
        data_dir = self.root / "data"
        data_dir.mkdir(parents=True)
        source_path = data_dir / "stamping_materials.xlsx"
        incoming_path = self.root / "incoming.xlsx"
        _material_book(source_path, model="OLD-MODEL", code="OLD-CODE")
        old_bytes = source_path.read_bytes()
        _material_book(incoming_path, model="NEW-MODEL", code="NEW-CODE")
        files = MaterialFileAdapter(
            data_dir=data_dir,
            material_data_path=source_path,
            template_path=data_dir / "template.xlsx",
            drawing_dir=data_dir / "material_drawings",
            lock_factory=_no_lock,
        )
        service = MaterialService(
            lambda: SQLiteMaterialUnitOfWork(self.database_path),
            lambda: None,
            files,
        )
        with patch.object(SQLiteMaterialRepository, "import_data", side_effect=RuntimeError("simulated failure")):
            with self.assertRaises(RuntimeError):
                service.import_data(incoming_path, original_name="incoming.xlsx", actor="module-test")
        self.assertEqual(source_path.read_bytes(), old_bytes)

        result = service.import_data(incoming_path, original_name="incoming.xlsx", actor="module-test")
        self.assertEqual(result.imported, 1)
        page = service.list_items(query="NEW-MODEL", status="active", limit=10, offset=0)
        self.assertEqual(page.total, 1)
        self.assertEqual(page.records[0]["code"], "NEW-CODE")

    def test_contract_service_enriches_product_and_compensates_failed_audit(self) -> None:
        with connect(self.database_path) as connection:
            upsert_product(
                connection,
                {
                    "bld_no": "K-CONTRACT-001",
                    "oe_no_1": "OE-CONTRACT-001",
                    "item": "Contract Part",
                    "models": "Contract Car",
                    "active": "1",
                },
                actor="module-test",
            )
        pdf = _PdfAdapter()
        product_service = ProductService(
            lambda: SQLiteProductUnitOfWork(self.database_path),
            lambda: None,
            lambda: {},
        )
        service = ContractService(
            lambda: SQLiteContractUnitOfWork(self.database_path),
            product_service,
            pdf,
            lambda _product: None,
        )
        product = service.lookup_product("k-contract-001")
        self.assertIsNotNone(product)
        form = MultiDict(
            [
                ("contract_no", "CT-SERVICE-001"),
                ("contract_date", "2026-07-11"),
                ("buyer_name", "BLD"),
                ("supplier_name", "Service Supplier"),
                ("product_code[]", "K-CONTRACT-001"),
                ("oe_no[]", "manual"),
                ("product_name[]", "manual"),
                ("models[]", "manual"),
                ("quantity[]", "2"),
                ("unit_price[]", "10"),
                ("delivery_date[]", "2026-07-20"),
                ("item_note[]", "test"),
            ]
        )
        output = service.generate("purchase", form, output_root=self.root / "outputs", actor="module-test")
        self.assertTrue(output.is_file())
        self.assertEqual(pdf.contract["items"][0]["oe_no"], "OE-CONTRACT-001")
        self.assertEqual(pdf.contract["items"][0]["product_name"], "Contract Part")

        failed_form = MultiDict(form)
        failed_form.setlist("contract_no", ["CT-SERVICE-FAIL"])
        with patch.object(SQLiteContractRepository, "audit", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                service.generate("purchase", failed_form, output_root=self.root / "outputs", actor="module-test")
        self.assertEqual(list((self.root / "outputs").rglob("*CT-SERVICE-FAIL*.pdf")), [])

    def test_shipping_service_upload_preview_generate_and_audit(self) -> None:
        store = ShippingTemplateStore(self.root / "shipping-templates")
        service = ShippingNoticeService(
            lambda: SQLiteShippingUnitOfWork(self.database_path),
            store,
            ShippingWorkbookAdapter(),
        )
        template_file = FileStorage(
            stream=io.BytesIO(_workbook_bytes([["客户", "商品编码", "数量"], ["ABC", "", ""]])),
            filename="service-template.xlsx",
        )
        template = service.upload_template(
            template_file,
            customer="Service Customer",
            name="Default",
            actor="module-test",
        )
        self.assertIsNotNone(store.find(str(template["id"])))

        shipment_path = self.root / "shipment.xlsx"
        shipment_path.write_bytes(_workbook_bytes([["商品编码", "数量"], ["K-SHIP-001", 12]]))
        preview = service.preview_shipment(template_id=str(template["id"]), upload_path=shipment_path)
        self.assertEqual(preview["row_count"], 1)
        output = service.generate(
            template_id=str(template["id"]),
            upload_path=shipment_path,
            output_dir=self.root / "outputs",
            actor="module-test",
        )
        workbook = load_workbook(output, data_only=True)
        try:
            self.assertEqual(workbook.active.cell(2, 2).value, "K-SHIP-001")
            self.assertEqual(workbook.active.cell(2, 3).value, 12)
        finally:
            workbook.close()
        with connect(self.database_path) as connection:
            actions = {row["action"] for row in connection.execute("SELECT action FROM audit_logs")}
        self.assertIn("上传发货通知模板", actions)
        self.assertIn("生成发货通知", actions)


if __name__ == "__main__":
    unittest.main()
