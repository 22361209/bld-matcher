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
