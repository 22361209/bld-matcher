from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from werkzeug.datastructures import FileStorage

from app.database import connect
from app.modules.business_sync.infrastructure import BusinessSyncRepository
from app.modules.costing.service import CostingService
from app.modules.development.document_service import DevelopmentDocumentService
from app.modules.development.file_store import DevelopmentFileStore
from app.modules.development.repository import SQLiteDevelopmentUnitOfWork
from app.modules.development.service import DevelopmentService
from app.modules.development.worksheet import default_worksheet


class CostingPackageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = root / "source.sqlite3"
        self.target = root / "target.sqlite3"
        self.backup = root / "before.sqlite3"
        self.package = root / "business-data.tar.gz"
        self.source_images, self.target_images = root / "source-images", root / "target-images"
        self.source_images.mkdir()
        self.target_images.mkdir()
        self.source_sync = BusinessSyncRepository(self.source, allow_costing=True, image_dir=self.source_images,
                                                  development_file_dir=root / "source-files")
        self.target_sync = BusinessSyncRepository(self.target, allow_costing=True, image_dir=self.target_images,
                                                  development_file_dir=root / "target-files")
        with connect(self.source) as connection:
            connection.execute(
                """INSERT INTO products(bld_no, image_path, created_at, updated_at)
                   VALUES('QA-PART', 'data_product_images/QA-PART_1.webp', '2030-01-01', '2030-01-01')"""
            )
        Image.new("RGB", (960, 720), "blue").save(self.source_images / "QA-PART_1.webp", format="WEBP")
        with connect(self.target):
            pass
        self.costs = CostingService(self.source)
        self.sheet = self.costs.create({"code": "QA-PART", "items": [
            {"name": "测试配件", "product_code": "QA-CODE", "material_attribute": "外购", "amount": "12.34"},
        ]}, actor="测试建档")
        self.sheet = self.costs.update(self.sheet["id"], {"remark": "备注修订"}, expected_version=1, actor="测试维护")

    def export(self, *datasets, **options):
        return self.source_sync.export(output_path=self.package, selected=datasets, actor="exporter", **options)

    def apply(self, token, **options):
        return self.target_sync.apply(self.package, backup_path=self.backup, expected_token=token,
                                      actor="importer", selected_conflicts={}, **options)

    def test_mixed_package_roundtrips_history_codes_attributes_and_structured_product_images(self):
        def development_unit_of_work():
            return SQLiteDevelopmentUnitOfWork(self.source)

        documents = DevelopmentDocumentService(development_unit_of_work, DevelopmentFileStore(self.source_sync.development_file_dir))
        project = DevelopmentService(development_unit_of_work).create_project({"core_number": "8686", "title": "同步测试"}, actor="owner")["project"]
        content = b"%PDF-1.4\nsynthetic costing sync fixture\n%%EOF"
        revision = documents.upload(project["id"], {"kind": "drawing", "number": "QA-TEST", "title": "测试图",
                                    "label": "A", "change_note": "同步测试", "state": "released"},
                                    FileStorage(stream=io.BytesIO(content), filename="fixture.pdf", content_type="application/pdf"),
                                    actor="owner", expected_version=project["version"])
        self.export("products", "costing", "development", include_images=True)
        self.assertEqual(BusinessSyncRepository.package_datasets(self.package), ("products", "costing", "development"))
        with tarfile.open(self.package) as archive:
            self.assertEqual(json.load(archive.extractfile("manifest.json"))["version"], 6)
            self.assertNotIn("costing", json.load(archive.extractfile("data.json")))
            self.assertEqual(len(json.load(archive.extractfile("costing.json"))["records"]), 1)
        preview = self.target_sync.preview(self.package)
        self.assertEqual(preview["summary"]["costing"]["counts"]["new"], 1)
        self.assertEqual(self.apply(preview["token"], include_images=True)["costing"]["new"], 1)
        target = CostingService(self.target)
        self.assertEqual(target.get(self.sheet["id"]), self.sheet)
        self.assertEqual(target.revisions(self.sheet["id"]), self.costs.revisions(self.sheet["id"]))
        with connect(self.target) as connection:
            image = connection.execute("SELECT image_path FROM products WHERE bld_no='QA-PART'").fetchone()[0]
        self.assertTrue((self.target_images / Path(image).name).is_file())
        expected_file = self.target_sync.development_file_dir / project["id"] / revision["id"] / (revision["sha256"] + ".pdf")
        self.assertEqual(expected_file.read_bytes(), content)
        preview = self.target_sync.preview(self.package)
        self.assertEqual(self.apply(preview["token"])["costing"]["unchanged"], 1)

    def test_current_stamping_references_protect_local_material_identity_during_sync(self):
        from app.modules.costing.material_prices import MaterialPriceService
        from app.modules.materials.item_store import upsert_material_item

        part = {"code": "PART440", "model": "QA", "part": "主板", "spec_text": "2x100x100", "pieces": "1"}
        with connect(self.source) as connection:
            identifier = upsert_material_item(connection, {**part, "spec_text": "2x110x100"})
            upsert_material_item(connection, {**part, "id": identifier})
            incoming_id = connection.execute("SELECT sync_id FROM material_items").fetchone()[0]
        with connect(self.target) as connection:
            upsert_material_item(connection, part)
            local_id = connection.execute("SELECT sync_id FROM material_items").fetchone()[0]
        self.assertNotEqual(incoming_id, local_id)
        self.export("materials")
        adoptable = self.target_sync.preview(self.package)
        self.assertEqual(adoptable["summary"]["materials"]["counts"]["updated"], 1)

        costs = CostingService(self.target)
        source = {"kind": "stamping", "product_code": "PART440", "name": "主板", "model": "QA", "spec": "2.0×100×100",
                  "source_id": local_id, "pieces": "1", "gross_weight": "4", "net_weight": "3", "scrap_weight": "1"}
        sheet = costs.create({"code": "QA-ARM", "items": [{"name": "主板", "mode": "weight", "product_code": "PART440",
                            "weight": "4", "unit_price": "3", "weight_source": source}]}, actor="qa")
        with self.assertRaisesRegex(ValueError, "本机数据已变化"):
            self.apply(adoptable["token"])
        for _ in range(2):
            protected = self.target_sync.preview(self.package)
            conflict, = protected["summary"]["materials"]["conflicts"]
            self.assertIs(conflict["selectable"], False)
            self.assertIn("正在使用这条材料取重", conflict["reason"])
            self.assertEqual(self.apply(protected["token"])["materials"]["conflict"], 1)
        with self.assertRaisesRegex(ValueError, "正在使用这条材料取重"):
            self.target_sync.apply(self.package, backup_path=self.backup, actor="qa", expected_token=protected["token"],
                                   selected_conflicts={"materials": {incoming_id}})
        with connect(self.target) as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT sync_id FROM material_items")], [local_id])
        prices = MaterialPriceService(costs)
        change = prices.request("SAPH440", "6")
        plan = prices.preview(change)
        self.assertEqual(plan["updated_rows"], 1)
        prices.apply(change, plan["digest"], actor="qa")
        updated = costs.get(sheet["id"])
        self.assertEqual(updated["items"][0]["unit_price"], "6")

        costs.update(sheet["id"], {"items": [{**updated["items"][0], "weight_source": None}]},
                     expected_version=updated["version"], actor="qa")
        with self.assertRaisesRegex(ValueError, "本机数据已变化"):
            self.apply(protected["token"])
        refreshed = self.target_sync.preview(self.package)
        self.assertEqual(self.apply(refreshed["token"])["materials"]["updated"], 1)
        self.assertEqual(self.target_sync.preview(self.package)["summary"]["materials"]["counts"]["unchanged"], 1)

    def test_derived_grade_and_weighted_cost_snapshot_roundtrip(self):
        from app.modules.materials.item_store import upsert_material_item

        with connect(self.source) as conn:
            upsert_material_item(conn, {"model": "QA", "code": "QA-STAMP", "part": "主板", "spec_text": "2×100×100",
                                       "pieces": "1", "net_weight": "0.1"})
        auto = self.costs.create({"code": "QA-WEIGHT", "scrap_mode": "auto", "scrap_credit_override": "0",
                                  "items": [{"name": "主板", "mode": "weight", "unit_price": "4", "weight": "1"}]}, actor="qa")
        self.export("materials", "costing")
        with tarfile.open(self.package) as archive:
            self.assertEqual(json.load(archive.extractfile("manifest.json"))["version"], 11)
            self.assertEqual(json.load(archive.extractfile("costing.json"))["format_version"], 3)
        preview = self.target_sync.preview(self.package)
        self.apply(preview["token"])
        self.assertEqual(CostingService(self.target).get(auto["id"]), auto)
        with connect(self.target) as conn:
            self.assertEqual(conn.execute("SELECT net_weight FROM material_items WHERE code='QA-STAMP'").fetchone()[0], "0.1")

    def test_v11_mixed_worksheet_grade_and_quote_role_survive_roundtrip(self):
        from app.modules.materials.item_store import upsert_material_item

        with connect(self.source) as conn:
            upsert_material_item(conn, {"model": "QA", "code": "QA-440-STAMP", "part": "主板", "spec_text": "2×100×100",
                                       "pieces": "1"})
        rows = [dict(self.sheet["items"][0], quote_role="weld")]
        tagged = self.costs.update(self.sheet["id"], {"items": rows}, expected_version=2, actor="qa")
        # A later manual correction restores auto classification; the history still requires a v4 reader.
        final = self.costs.update(tagged["id"], {"items": [dict(tagged["items"][0], quote_role="")]},
                                  expected_version=3, actor="qa")
        self.assertEqual(tagged["calculated_cost"], final["calculated_cost"])
        self.assertNotIn("quote_role", final["items"][0])
        development = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(self.source))
        development.create_worksheet({"core_code": "8274", "started": False, "rows": []}, actor="qa")
        self.export("materials", "costing", "development")
        preview = self.target_sync.preview(self.package)
        self.assertEqual(preview["manifest"]["version"], 11)
        with tarfile.open(self.package) as archive:
            self.assertEqual(json.load(archive.extractfile("costing.json"))["format_version"], 4)
        self.apply(preview["token"])
        with connect(self.target) as conn:
            self.assertEqual(conn.execute("SELECT material_grade FROM material_items WHERE code='QA-440-STAMP'").fetchone()[0], "SAPH440")
        self.assertEqual(CostingService(self.target).revisions(tagged["id"]), self.costs.revisions(tagged["id"]))

        new = development.create_worksheet(dict(default_worksheet(), core_code="8275"), actor="qa")
        self.export("materials", "costing", "development")
        preview = self.target_sync.preview(self.package)
        self.assertEqual(preview["manifest"]["version"], 12)
        self.assertEqual(self.apply(preview["token"])["development"]["new"], 1)
        target = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(self.target))
        self.assertEqual(target.detail(new["project"]["id"])["worksheet"], new["worksheet"])

    def test_v10_keeps_mixed_costs_and_structured_product_images(self):
        development = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(self.source))
        sheet = {"core_code": "8274", "started": False, "rows": [{"name": "主体", "material": "铸铁"}]}
        development.create_worksheet(sheet, actor="owner")
        self.export("costing", "development", "products", include_images=True)
        preview = self.target_sync.preview(self.package)
        self.assertEqual(preview["manifest"]["version"], 10)
        result = self.apply(preview["token"], include_images=True)
        self.assertEqual(result["development"]["new"], 1)
        self.assertEqual(result["costing"]["new"], 1)
        with connect(self.target) as connection:
            image = connection.execute("SELECT image_path FROM products WHERE bld_no='QA-PART'").fetchone()[0]
        self.assertTrue((self.target_images / Path(image).name).is_file())

    def test_writes_share_transaction_and_media_rollback(self):
        self.export("costing", "products", include_images=True)
        preview = self.target_sync.preview(self.package)
        # Audit runs after the cost rows and product image have been written.
        code = self.target_sync.apply.__func__.__globals__["apply_package"].__globals__
        with patch.dict(code, {"log_event": Mock(side_effect=RuntimeError("audit"))}):
            with self.assertRaisesRegex(RuntimeError, "audit"):
                self.apply(preview["token"], include_images=True)
        with connect(self.target) as connection:
            for table in ("products", "costing_records", "costing_revisions"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertFalse(any(self.target_images.rglob("*.webp")))
        self.assertTrue(self.backup.is_file())
        self.assertEqual(self.apply(preview["token"], include_images=True)["costing"]["new"], 1)

    def test_local_cost_edits_after_preview_reject_whole_mixed_import(self):
        self.export("products", "costing")
        preview = self.target_sync.preview(self.package)
        CostingService(self.target).create({"code": "QA-LOCAL"}, actor="本机管理员")
        with self.assertRaisesRegex(ValueError, "变化"):
            self.apply(preview["token"])
        with connect(self.target) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM products WHERE bld_no='QA-PART'").fetchone())
            self.assertEqual([r[0] for r in connection.execute("SELECT code FROM costing_records")], ["QA-LOCAL"])

    def test_costs_are_opt_in_and_existing_non_cost_exports_remain_v5(self):
        self.export("products")
        with tarfile.open(self.package) as archive:
            self.assertNotIn("costing.json", archive.getnames())
            self.assertEqual(json.load(archive.extractfile("manifest.json"))["version"], 5)
        denied = BusinessSyncRepository(self.target)
        with self.assertRaisesRegex(ValueError, "权限"):
            denied.export(output_path=self.package, selected=("costing",), actor="viewer")
        self.export("costing")
        token = self.target_sync.preview(self.package)["token"]
        with self.assertRaisesRegex(ValueError, "权限"):
            denied.preview(self.package)
        with self.assertRaisesRegex(ValueError, "权限"):
            denied.apply(self.package, backup_path=self.backup, expected_token=token, actor="viewer", selected_conflicts={})
        self.assertFalse(self.backup.exists())

    def test_cost_member_and_manifest_must_agree_and_validate_before_other_data(self):
        self.export("products", "costing")
        with tarfile.open(self.package) as archive:
            files = {name: json.load(archive.extractfile(name)) for name in archive.getnames()}
        for mode in ("missing", "unexpected", "bad-version", "altered", "inline"):
            with self.subTest(mode=mode):
                members = {**files, "manifest.json": dict(files["manifest.json"]), "data.json": dict(files["data.json"])}
                if mode == "missing":
                    del members["costing.json"]
                elif mode == "unexpected":
                    members["manifest.json"]["datasets"] = ["products"]
                elif mode == "bad-version":
                    members["manifest.json"]["version"] = 5
                elif mode == "inline":
                    members["data.json"]["costing"] = [members.pop("costing.json")]
                else:
                    members["costing.json"] = {**members["costing.json"], "digest": "0" * 64}
                with tarfile.open(self.package, "w:gz") as archive:
                    for name, data in members.items():
                        content = json.dumps(data).encode()
                        entry = tarfile.TarInfo(name)
                        entry.size = len(content)
                        archive.addfile(entry, io.BytesIO(content))
                with self.assertRaises(ValueError):
                    self.target_sync.preview(self.package)
                self.assertFalse(self.backup.exists())
