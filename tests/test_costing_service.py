from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.modules.costing import repository as costing_repository
from app.modules.costing.domain import MAX_REFERENCE_BYTES, CostingConflict, CostingNotFound, CostingValidationError
from app.modules.costing.repository import initialize_costing_database
from app.modules.costing.service import CostingService


class CostingServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "costs.sqlite3"
        initialize_costing_database(self.path)
        self.service = CostingService(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def sheet(code="Q8001", **overrides):
        return {"code": code, "kind": "ball", "name": "球头", "models": "ix35",
                "items": [{"name": "螺栓", "mode": "unit", "unit_price": "0.25", "quantity": "3"},
                          {"name": "其余已核定明细", "mode": "fixed", "amount": "9.97"}], **overrides}

    def test_real_change_persists_and_copy_stays_independent(self):
        original = self.service.create(self.sheet(), actor="lin")
        self.assertEqual(original["calculated_cost"], "10.72")
        copied = self.service.create({**original, "code": "Q8001-COPY"}, actor="lin", source="copy")
        items = original["items"]
        items[0]["unit_price"] = "0.30"
        after = self.service.update(original["id"], {"items": items}, expected_version=1, actor="lin")
        reopened = CostingService(self.path).get(original["id"])
        self.assertEqual(after, reopened)
        self.assertEqual(reopened["calculated_cost"], "10.87")
        self.assertEqual(self.service.get(copied["id"])["calculated_cost"], "10.72")
        self.assertNotEqual(copied["items"][0]["id"], original["items"][0]["id"])
        history = self.service.revisions(original["id"])
        self.assertEqual([row["action"] for row in history], ["update", "create"])
        self.assertEqual(history[0]["before"]["calculated_cost"], "10.72")
        self.assertEqual(history[0]["after"]["calculated_cost"], "10.87")
        self.assertEqual(history[0]["actor"], "lin")

    def test_duplicate_code_and_rename_cannot_overwrite_existing_cost(self):
        original = self.service.create(self.sheet(), actor="lin")
        with self.assertRaises(CostingConflict):
            self.service.create(self.sheet("q8001"), actor="lin")
        second = self.service.create(self.sheet("Q8002"), actor="lin")
        with self.assertRaises(CostingConflict):
            self.service.update(second["id"], {"code": "Q8001"}, expected_version=1, actor="lin")
        self.assertEqual(self.service.get(original["id"]), original)
        self.assertEqual(self.service.get(second["id"]), second)

    def test_two_editors_cannot_silently_overwrite(self):
        original = self.service.create(self.sheet(), actor="lin")

        def edit(remark):
            try:
                return self.service.update(original["id"], {"remark": remark}, expected_version=1, actor=remark)
            except CostingConflict as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(edit, ["first", "second"]))
        self.assertEqual(sum(isinstance(value, CostingConflict) for value in outcomes), 1)
        self.assertEqual(self.service.get(original["id"])["version"], 2)
        self.assertEqual(len(self.service.revisions(original["id"])), 2)

    def test_import_is_all_or_nothing_and_does_not_overwrite(self):
        self.service.create(self.sheet(), actor="lin")
        with self.assertRaises(CostingConflict):
            self.service.import_records([self.sheet("QNEW"), self.sheet()], actor="importer")
        self.assertEqual(self.service.list()["total"], 1)
        self.assertEqual(self.service.list(q="QNEW")["total"], 0)
        result = self.service.import_records([self.sheet("QNEW"), self.sheet("QNEW2")], actor="importer")
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["records"][0]["source"], "excel")
        with self.assertRaises(CostingConflict):
            self.service.import_records([self.sheet("QX"), self.sheet("qx")], actor="importer")
        self.assertEqual(self.service.list()["total"], 3)

    def test_revision_failure_rolls_back_both_create_and_update(self):
        original = self.service.create(self.sheet(), actor="lin")
        # Web fixtures can reload app modules; patch the class captured by this
        # service rather than resolving a newer copy through sys.modules.
        with patch.object(costing_repository.SQLiteCostingRepository, "add_revision", side_effect=RuntimeError("audit")):
            with self.assertRaisesRegex(RuntimeError, "audit"):
                self.service.create(self.sheet("QNEW"), actor="lin")
            with self.assertRaisesRegex(RuntimeError, "audit"):
                self.service.update(original["id"], {"remark": "must rollback"}, expected_version=1, actor="lin")
        self.assertEqual(self.service.list()["total"], 1)
        self.assertEqual(self.service.get(original["id"]), original)

    def test_line_code_cleanup_is_atomic_preserves_totals_and_exports_history(self):
        from app.modules.costing.transfer_service import CostingTransferService

        cartons = self.service.create(self.sheet("A", items=[{
            "category": "外箱", "name": "31*22*12.5", "part_code": "QA-BOX-007",
            "mode": "fixed", "amount": "1.234567", "source_info": {"cached": "1.234567"},
        }]), actor="owner", source="excel")
        bushings = self.service.create(self.sheet("B", items=[{
            "category": "衬套", "name": "C9907", "amount": "3.21",
        }]), actor="owner", source="excel")
        preview = self.service.preview_line_codes()
        self.assertEqual((preview["records"], preview["items"], preview["cartons"], preview["codes"]), (2, 2, 1, 1))
        self.assertEqual(preview["attributes"], {"自制": 0, "外购": 2, "委外加工": 0})
        changed = self.service.update(bushings["id"], {"remark": "库存复核"}, expected_version=1, actor="owner")
        with self.assertRaises(CostingConflict):
            self.service.apply_line_codes(preview["digest"], actor="admin")
        preview = self.service.preview_line_codes()
        repo = costing_repository.SQLiteCostingRepository
        add_revision = repo.add_revision

        def fail_second(repository, before, after, **kwargs):
            if after["code"] == "B":
                raise RuntimeError("audit unavailable")
            return add_revision(repository, before, after, **kwargs)

        with patch.object(repo, "add_revision", fail_second), self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            self.service.apply_line_codes(preview["digest"], actor="admin")
        self.assertEqual(self.service.get(cartons["id"]), cartons)
        self.assertEqual(self.service.get(bushings["id"]), changed)
        self.assertEqual(self.service.apply_line_codes(preview["digest"], actor="admin"), preview)
        snapshot = CostingTransferService(self.path).export_snapshot()
        self.assertEqual(len(snapshot["revisions"]), 5)
        for before, patch_fields in [(cartons, {"category": "外包装", "name": "纸箱", "product_code": "31*22*12.5"}),
                                     (changed, {"product_code": "C9907"})]:
            history = self.service.revisions(before["id"])
            after = history[0]["after"]
            self.assertEqual(history[0]["actor"], "admin")
            self.assertEqual(history[0]["before"], before)
            self.assertEqual(after["items"], [{**before["items"][0], **patch_fields, "material_attribute": "外购"}])
            self.assertEqual(after, {**before, "version": before["version"] + 1, "updated_at": after["updated_at"],
                                     "updated_by": "admin", "items": after["items"]})
        self.assertEqual(self.service.apply_line_codes(preview["digest"], actor="admin")["items"], 0)
        self.assertEqual(CostingTransferService(self.path).export_snapshot(), snapshot)

    def test_reference_prices_commit_and_roll_back_with_the_imported_records(self):
        original_references = {"cartons": [{"code": "BOX", "unit_price": "4.5"}]}
        self.service.import_records([self.sheet()], actor="importer", references=original_references)
        self.assertEqual(self.service.references(), original_references)
        with self.assertRaises(CostingConflict):
            self.service.import_records([self.sheet("QNEW"), self.sheet()], actor="importer",
                                        references={"cartons": []})
        self.assertEqual(self.service.references(), original_references)
        self.assertEqual(self.service.list()["total"], 1)
        self.service.import_records([self.sheet("QNEW")], actor="importer")
        self.assertEqual(self.service.references(), original_references)
        changed = {"cartons": [{"code": "BOX", "unit_price": "5"}]}
        self.service.import_records([self.sheet("QNEW2")], actor="importer", references=changed)
        self.assertEqual(CostingService(self.path).references(), changed)

    def test_workbook_reference_budget_is_separate_from_per_line_source_budget(self):
        references = {"cartons": [{"code": "BOX", "unit_price": "4.5"}], "source_rows": "参考" * 70_000}
        result = self.service.import_records([self.sheet()], actor="importer", references=references)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(CostingService(self.path).references(), references)
        with self.assertRaises(CostingConflict):
            self.service.import_records([self.sheet("QNEW"), self.sheet()], actor="importer",
                                        references={"source_rows": "变更" * 70_000})
        self.assertEqual(self.service.references(), references)
        self.assertEqual(self.service.list()["total"], 1)
        # Chinese text exceeds the byte budget even though its character count is smaller.
        oversized = {"source_rows": "中" * (MAX_REFERENCE_BYTES // 3 + 1)}
        with self.assertRaisesRegex(CostingValidationError, "8 MiB"):
            self.service.import_records([self.sheet("QTOOBIG")], actor="importer", references=oversized)
        with self.assertRaisesRegex(CostingValidationError, "来源资料过大"):
            self.service.create(self.sheet("QSOURCE", source_info={"raw": "x" * 128_001}), actor="lin")
        with self.assertRaisesRegex(CostingValidationError, "参考资料格式无效"):
            self.service.import_records([self.sheet("QNAN")], actor="importer", references={"price": float("nan")})
        self.assertEqual(self.service.list()["total"], 1)
        self.assertEqual(self.service.references(), references)

    def test_attribution_and_source_snapshots_cannot_be_replaced_on_update(self):
        raw = self.sheet(source_info={"sheet": "球头成本核算"})
        raw["items"][0]["source_info"] = {"row": 29, "formula": "=I29*J29"}
        original = self.service.create(raw, actor="trusted", source="excel")
        items = original["items"]
        items[0]["source_info"] = {"row": 999}
        after = self.service.update(original["id"], {
            "items": items, "created_by": "spoof", "updated_by": "spoof", "source": "manual",
            "source_info": {"sheet": "spoof"},
        }, expected_version=1, actor="real-editor")
        self.assertEqual(after["created_by"], "trusted")
        self.assertEqual(after["updated_by"], "real-editor")
        self.assertEqual(after["source"], "excel")
        self.assertEqual(after["source_info"], {"sheet": "球头成本核算"})
        self.assertEqual(after["items"][0]["source_info"]["row"], 29)

    def test_search_pagination_drafts_and_empty_result(self):
        for code in ("K_01", "KX01", "K_02"):
            self.service.create(self.sheet(code), actor="lin")
        draft = self.service.create(self.sheet("DRAFT", items=[]), actor="lin")
        self.assertEqual(self.service.list(q="_")["total"], 2)
        page = self.service.list(page=99, page_size=2, sort="code")
        self.assertEqual((page["page"], page["pages"], page["total"]), (2, 2, 4))
        self.assertEqual(self.service.list(status="draft")["records"][0]["id"], draft["id"])
        self.assertEqual(self.service.list(q="不存在")["records"], [])
        with self.assertRaises(CostingNotFound):
            self.service.get("missing")

    def test_initialization_is_explicit_idempotent_and_rejects_other_databases(self):
        original = self.service.create(self.sheet(), actor="lin")
        initialize_costing_database(self.path)
        self.assertEqual(self.service.get(original["id"]), original)
        foreign = Path(self.tmp.name) / "unrelated.sqlite3"
        with sqlite3.connect(foreign) as conn:
            conn.execute("CREATE TABLE products(id INTEGER)")
        with self.assertRaisesRegex(ValueError, "独立"):
            initialize_costing_database(foreign)
        with sqlite3.connect(foreign) as conn:
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("products",)])
        absent = Path(self.tmp.name) / "absent.sqlite3"
        service = CostingService(absent)
        self.assertFalse(absent.exists())
        with self.assertRaises(sqlite3.OperationalError):
            service.list()
        self.assertFalse(absent.exists())

    def test_interrupted_initialization_rolls_back_schema_and_is_retryable(self):
        path = Path(self.tmp.name) / "interrupted.sqlite3"
        real_connect = sqlite3.connect

        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.startswith("INSERT INTO costing_schema"):
                    raise RuntimeError("simulated interruption")
                return super().execute(sql, parameters)

        with patch.object(costing_repository.sqlite3, "connect",
                   side_effect=lambda filename: real_connect(filename, factory=FailingConnection)):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                initialize_costing_database(path)
        with real_connect(path) as conn:
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
        initialize_costing_database(path)
        self.assertEqual(CostingService(path).list()["total"], 0)


if __name__ == "__main__":
    unittest.main()
