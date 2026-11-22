from __future__ import annotations

import io
import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import SCHEMA, connect
from app.migrations import MIGRATIONS, run_migrations
from app.modules.business_sync.infrastructure import BusinessSyncRepository
from app.modules.costing.service import CostingService
from app.modules.development.repository import SQLiteDevelopmentUnitOfWork
from app.modules.development.service import DevelopmentService


class TubeWeightSyncTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.target = self.root / "source.sqlite3", self.root / "target.sqlite3"
        for path in (self.source, self.target):
            with connect(path):
                pass
        self.src = BusinessSyncRepository(self.source, allow_costing=True, development_file_dir=self.root / "source-files")
        self.dst = BusinessSyncRepository(self.target, allow_costing=True, development_file_dir=self.root / "target-files")

    def seed(self, path=None, **overrides):
        row = {"code": "KE-TEST", "tube_type": "普通管", "spec_text": "ø41×34.5×41",
               "weight_mode": "auto", "weight_length_mm": 41.5, "weight_kg": 0.1353899503125,
               "outer_diameter_mm": 41, "inner_diameter_mm": 34.5, "tolerance_mm": .25, "consumption_mm": 3,
               "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00", **overrides}
        with connect(path or self.source) as conn:
            conn.execute(f"INSERT INTO tube_items ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))

    def export(self, *datasets):
        return self.src.export(output_path=self.root / "export.tar.gz", selected=datasets or ("tubes",), actor="test")

    def apply(self, package):
        preview = self.dst.preview(package)
        return self.dst.apply(package, backup_path=self.root / "backup.sqlite3", actor="test",
                              expected_token=preview["token"], selected_conflicts={})

    def target_row(self):
        with connect(self.target) as conn:
            return dict(conn.execute("SELECT * FROM tube_items WHERE code='KE-TEST'").fetchone())

    def rewrite(self, package, change, *, version=None):
        with tarfile.open(package) as archive:
            contents = {m.name: archive.extractfile(m).read() for m in archive.getmembers()}
        manifest, data = json.loads(contents["manifest.json"]), json.loads(contents["data.json"])
        change(data)
        if version is not None:
            manifest["version"] = version
        contents.update({"manifest.json": json.dumps(manifest).encode(), "data.json": json.dumps(data).encode()})
        path = self.root / "edited.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, content in contents.items():
                item = tarfile.TarInfo(name)
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
        return path

    def test_default_manual_export_keeps_old_format_and_roundtrips(self):
        self.seed(weight_mode="manual", weight_length_mm=None, weight_kg=.2345)
        package = self.export()
        manifest, payload = self.src.read(package)
        self.assertEqual(manifest["version"], 5)
        self.assertNotIn("weight_mode", payload["tubes"][0])
        self.assertNotIn("weight_length_mm", payload["tubes"][0])
        self.apply(package)
        row = self.target_row()
        self.assertEqual((row["weight_mode"], row["weight_length_mm"], row["weight_kg"]), ("manual", None, .2345))

    def test_auto_and_manual_rows_roundtrip_in_v14_and_auto_cache_is_recomputed(self):
        self.seed(weight_kg=999)
        self.seed(code="MANUAL", weight_mode="manual", weight_length_mm=None, weight_kg=.6)
        package = self.export()
        manifest, payload = self.src.read(package)
        self.assertEqual(manifest["version"], 14)
        self.assertTrue(all("weight_mode" in row and "weight_length_mm" in row for row in payload["tubes"]))
        self.assertAlmostEqual(payload["tubes"][0]["weight_kg"], .1353899503125)
        tampered = self.rewrite(package, lambda data: data["tubes"][0].update(weight_kg=321))
        self.apply(tampered)
        row = self.target_row()
        self.assertEqual((row["weight_mode"], row["weight_length_mm"]), ("auto", 41.5))
        self.assertAlmostEqual(row["weight_kg"], .1353899503125)
        self.assertEqual(self.dst.preview(tampered)["summary"]["tubes"]["counts"]["unchanged"], 2)
        with connect(self.target) as conn:
            self.assertEqual(conn.execute("SELECT weight_kg FROM tube_items WHERE code='MANUAL'").fetchone()[0], .6)

    def test_manual_saved_calculation_length_requires_new_format(self):
        self.seed(weight_mode="manual", weight_kg=.7)
        package = self.export()
        self.assertEqual(self.src.read(package)[0]["version"], 14)
        self.apply(package)
        self.assertEqual((self.target_row()["weight_length_mm"], self.target_row()["weight_kg"]), (41.5, .7))

    def test_v14_kg6028_manual_precision_and_other_kg_auto_roundtrip(self):
        precision = 0.123456789012345
        self.seed(code="KG6028", weight_mode="manual", weight_kg=precision)
        self.seed(code="KG8097")
        package = self.export()
        self.assertEqual(self.src.read(package)[0]["version"], 14)
        self.apply(package)
        with connect(self.target) as conn:
            rows = {row["code"]: dict(row) for row in conn.execute("SELECT * FROM tube_items")}
        self.assertEqual((rows["KG6028"]["weight_mode"], rows["KG6028"]["weight_kg"]), ("manual", precision))
        self.assertEqual(rows["KG8097"]["weight_mode"], "auto")
        self.assertAlmostEqual(rows["KG8097"]["weight_kg"], .1353899503125)

    def test_v14_forged_auto_kg6028_is_rejected_without_partial_writes(self):
        self.seed(code="KG6028", weight_mode="manual", weight_kg=.123456789012345)
        self.seed(code="KG8097")
        self.seed(self.target, code="KG6028", weight_mode="manual", weight_kg=.987654321012345,
                  note="目标端原始记录")
        package = self.export()
        token = self.dst.preview(package)["token"]
        with connect(self.target) as conn:
            before = [dict(row) for row in conn.execute("SELECT * FROM tube_items ORDER BY code")]
        for code in ("KG6028", "kg6028", "Kg6028"):
            def forge(data):
                row = next(row for row in data["tubes"] if row["code"] == "KG6028")
                row.update(code=code, weight_mode="auto", weight_kg=999)

            tampered = self.rewrite(package, forge)
            for operation in ("preview", "apply"):
                with self.subTest(code=code, operation=operation):
                    with self.assertRaises(ValueError) as caught:
                        if operation == "preview":
                            self.dst.preview(tampered)
                        else:
                            self.dst.apply(tampered, backup_path=self.root / "rejected-backup.sqlite3", actor="test",
                                           expected_token=token, selected_conflicts={})
                    self.assertIn("KG6028", str(caught.exception))
                    self.assertIn("手工", str(caught.exception))
                    with connect(self.target) as conn:
                        after = [dict(row) for row in conn.execute("SELECT * FROM tube_items ORDER BY code")]
                    self.assertEqual(after, before)

    def test_missing_auto_input_is_preserved_as_unknown_weight(self):
        self.seed(weight_length_mm=None, weight_kg=999)
        self.apply(self.export())
        self.assertIsNone(self.target_row()["weight_kg"])
        self.assertEqual(self.target_row()["weight_mode"], "auto")

    def test_auto_numeric_strings_and_blank_inputs_roundtrip_idempotently(self):
        self.seed()
        package = self.export()
        numeric = {"outer_diameter_mm": "41", "inner_diameter_mm": "34.5",
                   "tolerance_mm": "0.25", "consumption_mm": "3", "weight_length_mm": "41.5"}
        string_package = self.rewrite(package, lambda data: data["tubes"][0].update(numeric))
        self.apply(string_package)
        self.assertAlmostEqual(self.target_row()["weight_kg"], .1353899503125)
        self.assertEqual(self.dst.preview(string_package)["summary"]["tubes"]["counts"]["unchanged"], 1)
        self.assertEqual(self.apply(string_package)["tubes"]["unchanged"], 1)

        def blanks(data):
            data["tubes"][0].update(numeric, consumption_mm="  ", tolerance_mm=None, updated_at="2026-02-01 00:00:00")

        blank_package = self.rewrite(package, blanks)
        self.apply(blank_package)
        row = self.target_row()
        self.assertIsNone(row["consumption_mm"])
        self.assertIsNone(row["tolerance_mm"])
        self.assertIsNone(row["weight_kg"])
        self.assertEqual(self.dst.preview(blank_package)["summary"]["tubes"]["counts"]["unchanged"], 1)

    def test_auto_borrowed_inputs_reject_non_numeric_text_without_rewriting_manual_dimensions(self):
        self.seed(borrowed_from="SOURCE")
        package = self.export()
        for value in ("not a number", True, float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "有限数字"):
                self.dst.preview(self.rewrite(package, lambda data: data["tubes"][0].update(outer_diameter_mm=value)))
        manual = self.rewrite(package, lambda data: data["tubes"][0].update(weight_mode="manual", outer_diameter_mm="原登记文本"))
        _, payload = self.src.read(manual)
        self.assertEqual(payload["tubes"][0]["outer_diameter_mm"], "原登记文本")

    def test_new_fields_cannot_be_downgraded_or_partially_removed(self):
        self.seed()
        package = self.export()
        with self.assertRaisesRegex(ValueError, "新版格式"):
            self.dst.preview(self.rewrite(package, lambda data: None, version=5))
        for missing in ("weight_mode", "weight_length_mm"):
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "计重字段"):
                self.dst.preview(self.rewrite(package, lambda data: data["tubes"][0].pop(missing)))
        for invalid in ({"weight_mode": "unknown"}, {"weight_length_mm": -2}, {"outer_diameter_mm": 30},
                        {"consumption_mm": float("inf")}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.dst.preview(self.rewrite(package, lambda data: data["tubes"][0].update(invalid)))

    def test_legacy_metadata_update_preserves_existing_calculation_mode_and_length(self):
        self.seed()
        package = self.export()
        self.apply(package)

        def metadata(data):
            row = data["tubes"][0]
            row.pop("weight_mode")
            row.pop("weight_length_mm")
            row.update(note="来源补充备注", updated_at="2026-02-01 00:00:00")

        legacy = self.rewrite(package, metadata, version=5)
        self.apply(legacy)
        row = self.target_row()
        self.assertEqual((row["weight_mode"], row["weight_length_mm"], row["note"]), ("auto", 41.5, "来源补充备注"))
        self.assertAlmostEqual(row["weight_kg"], .1353899503125)

    def test_legacy_package_cannot_mix_changed_weight_inputs_into_auto_record(self):
        self.seed()
        package = self.export()
        self.apply(package)
        before = self.target_row()
        for field, value in (("outer_diameter_mm", 42), ("inner_diameter_mm", 34), ("tolerance_mm", 1),
                             ("consumption_mm", 2), ("weight_kg", .2), ("borrowed_from", "OTHER")):
            def change(data):
                row = data["tubes"][0]
                row.pop("weight_mode")
                row.pop("weight_length_mm")
                row.update({field: value, "updated_at": "2026-02-01 00:00:00"})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "旧版业务包不能修改自动计重管件 KE-TEST"):
                self.dst.preview(self.rewrite(package, change, version=5))
            self.assertEqual(self.target_row(), before)

    def test_v14_mixed_completed_worksheet_and_historical_costs_roundtrip(self):
        self.seed()
        costs = CostingService(self.source)
        source = {"kind": "tube", "product_code": "KE-TEST", "name": "普通管", "model": "", "spec": "旧规格",
                  "source_id": "KE-TEST", "pieces": None, "gross_weight": "0.22", "net_weight": None, "scrap_weight": None}
        cost = costs.create({"code": "HISTORY", "items": [{"name": "焊接管", "product_code": "KE-TEST", "mode": "weight",
                                                          "unit_price": "6", "weight_source": source}]}, actor="test")
        dev = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(self.source))
        project = dev.create_worksheet({"core_code": "FINISHED", "started": True, "completed": True,
                                        "rows": [{"name": "主体", "material": "自定义材料"}]}, actor="test")
        package = self.export("tubes", "costing", "development")
        self.assertEqual(self.src.read(package)[0]["version"], 14)
        result = self.apply(package)
        self.assertEqual([result[key]["new"] for key in ("tubes", "costing", "development")], [1, 1, 1])
        target_costs = CostingService(self.target)
        self.assertEqual(target_costs.get(cost["id"]), cost)
        self.assertEqual(target_costs.revisions(cost["id"]), costs.revisions(cost["id"]))
        target_dev = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(self.target))
        self.assertEqual(target_dev.detail(project["project"]["id"])["worksheet"], project["worksheet"])
        without_tubes = self.export("costing", "development")
        self.assertEqual(self.src.read(without_tubes)[0]["version"], 13)


class TubeWeightMigrationTest(unittest.TestCase):
    def test_historical_upgrade_preserves_every_original_value_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "before050.sqlite3"
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            old_schema = SCHEMA.replace("  weight_mode TEXT NOT NULL DEFAULT 'manual',\n", "").replace("  weight_length_mm REAL,\n", "")
            conn.executescript(old_schema)
            with patch("app.migrations.MIGRATIONS", MIGRATIONS[:-1]):
                run_migrations(conn)
            conn.execute("""INSERT INTO tube_items(code, weight_kg, outer_diameter_mm, inner_diameter_mm,
                         blank_length_text, created_at, updated_at) VALUES('OLD', .777, 41, 34.5, '41±0.1', 'old', 'old')""")
            conn.commit()
            before = dict(conn.execute("SELECT * FROM tube_items").fetchone())
            run_migrations(conn)
            run_migrations(conn)
            after = dict(conn.execute("SELECT * FROM tube_items").fetchone())
            self.assertEqual({k: after[k] for k in before}, before)
            self.assertEqual((after["weight_mode"], after["weight_length_mm"]), ("manual", None))
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations WHERE id='050_tube_weight_calculation'").fetchone()[0], 1)
            conn.close()
