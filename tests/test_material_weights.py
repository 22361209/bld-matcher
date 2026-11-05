from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from app.database import connect
from app.modules.materials.excel_import import import_materials_from_excel
from app.modules.materials.item_store import get_material_item, list_material_items, upsert_material_item
from app.modules.materials.repository import SQLiteMaterialRepository


class MaterialWeightsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        self.addCleanup(self.conn.close)
        self.repo = SQLiteMaterialRepository(self.conn)
        self.row = {"model": "8057", "code": "KA8057", "part": "主板", "spec_text": "2.5×357×1260", "pieces": "4"}

    def test_exact_active_lookup_and_net_weight_edits(self):
        item_id = upsert_material_item(self.conn, self.row | {"net_weight": "0.95"})
        upsert_material_item(self.conn, self.row | {"code": "KA80570"})
        inactive_id = upsert_material_item(self.conn, self.row | {"active": "0"})
        rows = self.repo.costing_candidates("ka8057")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0] | {"scrap_percent": None}, {
            "code": "KA8057", "name": "主板", "model": "8057", "spec": "2.5×357×1260", "pieces": "4.0",
            "sync_id": get_material_item(self.conn, item_id)["sync_id"], "gross_weight": "2.206929375",
            "net_weight": "0.95", "scrap_weight": "1.256929375", "scrap_percent": None,
        })
        self.assertAlmostEqual(float(rows[0]["scrap_percent"]), 56.953764138, places=3)
        lists = list_material_items(self.conn, query="主板", column_filters={"net_weight": ("0.95",)})
        self.assertEqual([(row["code"], row["scrap_weight"], row["scrap_percent"]) for row in lists],
                         [("KA8057", "1.2569", "56.95")])
        upsert_material_item(self.conn, self.row | {"id": item_id})  # Existing editor missing the optional field.
        self.assertEqual(get_material_item(self.conn, item_id)["net_weight"], "0.95")
        for invalid in ("NaN", "-1", "bad", "2.21", "1e-19"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                upsert_material_item(self.conn, self.row | {"id": item_id, "net_weight": invalid})
        upsert_material_item(self.conn, self.row | {"id": item_id, "net_weight": ""})
        selected, = self.repo.costing_candidates("KA8057")
        self.assertIsNone(selected["net_weight"])
        self.assertIsNone(selected["scrap_weight"])
        self.assertIsNone(selected["scrap_percent"])
        upsert_material_item(self.conn, self.row | {"id": inactive_id, "active": "1"})
        self.assertEqual(len(self.repo.costing_candidates("KA8057")), 2)

    def test_lookup_omits_unusable_legacy_weights_and_missing_material_identity(self):
        valid = upsert_material_item(self.conn, self.row)
        missing = upsert_material_item(self.conn, self.row)
        self.conn.execute("UPDATE material_items SET sync_id = '' WHERE id = ?", (missing,))
        upsert_material_item(self.conn, self.row | {"spec_text": "0×357×1260"})
        self.assertEqual([row["sync_id"] for row in self.repo.costing_candidates("KA8057")],
                         [get_material_item(self.conn, valid)["sync_id"]])

    def workbook(self, width, extra_headers=(), extra_values=()):
        book = Workbook()
        sheet = book.active
        sheet.title = "材料数据"
        sheet.append(["母件", "零件", "类别", "车型", "名称", "", "只数", "单重", "厚", "宽", "长", *extra_headers])
        sheet.append(["8057", "KA8057", "", "", "主板", "", 4, 3.45, 2.5, width, 1260, *extra_values])
        path = self.root / "materials.xlsx"
        book.save(path)
        book.close()
        return path

    def test_optional_explicit_net_column_and_old_workbooks_after_spec_change(self):
        import_materials_from_excel(self.conn, self.workbook(357))
        self.assertIsNone(self.repo.costing_candidates("KA8057")[0]["net_weight"])
        import_materials_from_excel(self.conn, self.workbook(357, ["备注", "净重（kg/件）"], ["", "0.954"]))
        import_materials_from_excel(self.conn, self.workbook(358))
        self.assertEqual(self.repo.costing_candidates("KA8057")[0]["net_weight"], "0.954")
        with self.assertRaisesRegex(ValueError, "单件下料重量"):
            import_materials_from_excel(self.conn, self.workbook(1))
        self.assertEqual(self.repo.costing_candidates("KA8057")[0]["spec"], "2.5×358×1260")
        upsert_material_item(self.conn, self.row | {"net_weight": "0.8"})
        with self.assertRaisesRegex(ValueError, "多条记录"):
            import_materials_from_excel(self.conn, self.workbook(359))
        self.assertEqual(len(self.repo.costing_candidates("KA8057")), 2)
