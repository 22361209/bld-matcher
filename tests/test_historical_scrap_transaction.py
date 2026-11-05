from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

from app.database import connect
from app.modules.costing.domain import CostingConflict, CostingValidationError
from app.modules.costing.historical_scrap_service import HistoricalScrapService
from app.modules.costing.service import CostingService
from app.modules.costing.workbook_import import parse_cost_workbook
from app.modules.materials.item_store import upsert_material_item
from tests.test_costing_import import add_arm, add_pivot, arm_book, write_fixture


class HistoricalScrapTransactionTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.db = root / "qa.sqlite3"
        self.path = root / "old.xlsx"
        self.material = {"model": "1001", "part": "主板", "code": "KA1001", "pieces": "1", "spec_text": "2×100×1000"}
        with connect(self.db) as conn:
            self.material["id"] = upsert_material_item(conn, self.material)
        self.book = arm_book()
        add_arm(self.book, 8, "主板", "=I8*K8", I=4, K=2)
        add_pivot(self.book, "K1001B", 8, 8, credit=1.9, theory=2, series=1001)
        sheet = self.book.create_sheet("片件重量")
        sheet.append(["产品编码", "产品名称", "单价", "毛重", "净重", "废料", "比例", "金额"])
        sheet.append([1001, "主板", 2, 2, 0.7, 1.3, 0.65, 2.6])
        write_fixture(self.path, self.book)
        parsed = parse_cost_workbook(self.path)
        self.costs = CostingService(self.db)
        self.costs.import_records(parsed["records"], actor="qa", references=parsed["references"])
        self.svc = HistoricalScrapService(self.db, "testing-key")

    def preview(self):
        with self.path.open("rb") as fp:
            return self.svc.preview(FileStorage(stream=fp, filename="source.xlsx"), owner="session1")

    def test_atomic_apply_state_binding_history_and_replay(self):
        preview = self.preview()
        self.assertEqual((preview["material_count"], preview["cost_count"]), (1, 1))
        digest = preview["digest"]
        with self.assertRaises(CostingValidationError):
            self.svc.apply(digest, owner="session2", actor="qa")
        with patch.object(CostingService, "_save_edit", side_effect=RuntimeError("simulated failure")):
            with self.assertRaises(RuntimeError):
                self.svc.apply(digest, owner="session1", actor="qa")
        with connect(self.db) as conn:
            self.assertIsNone(conn.execute("SELECT net_weight FROM material_items").fetchone()[0])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM costing_revisions").fetchone()[0], 1)
        with connect(self.db) as conn:
            upsert_material_item(conn, {**self.material, "spec_text": "2×110×1000"})
        with self.assertRaises(CostingConflict):
            self.svc.apply(digest, owner="session1", actor="qa")
        fresh = self.preview()
        self.assertEqual(fresh["costs"][0]["new_credit"], "1.054")
        self.svc.apply(fresh["digest"], owner="session1", actor="qa")
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT net_weight FROM material_items").fetchone()[0], "0.7")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM costing_revisions").fetchone()[0], 2)
            detail = conn.execute("SELECT detail FROM audit_logs WHERE action='补录材料净重'").fetchone()[0]
            self.assertIn("片件重量!E2", detail)
        with self.assertRaises(CostingConflict):
            self.svc.apply(fresh["digest"], owner="session1", actor="qa")
        after = self.preview()
        self.assertEqual((after["material_count"], after["cost_count"]), (0, 0))

    def test_original_source_identity_is_required_even_for_identical_plate_cells(self):
        self.book.create_sheet("附注")
        write_fixture(self.path, self.book)
        with self.assertRaisesRegex(CostingValidationError, "指纹"):
            self.preview()
