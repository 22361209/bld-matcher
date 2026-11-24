from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook, load_workbook

from app.database import connect
from app.material_sheet import PLAN_HEADERS, calculate, generate_material_sheet_from_materials
from app.modules.materials.bom_query import MaterialBOM
from app.modules.materials.bom_store import migrate_rows, update_bom, validate_graph
from app.modules.materials.item_store import upsert_material_item
from app.modules.materials.repository import SQLiteMaterialRepository
from tests.web_app_test_base import WebAppTestBase


class SharedMaterialBOMTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        self.addCleanup(self.conn.close)

    def create(self, code, name, kind, **fields):
        update_bom(self.conn, dict(action="create", code=code, name=name, kind=kind, qty=1, **fields))

    def legacy(self, model, code, **fields):
        row = dict(model=model, code=code, part="主板", pieces=4, spec_text="3.5×416×126")
        upsert_material_item(self.conn, row | fields)

    def test_migration_shares_archives_and_preserves_conflicts_and_old_quantities(self):
        self.legacy("8234", "KA")
        self.legacy("8235", "KA", net_weight="0.25", active="0")
        self.legacy("8234", "DUP")
        self.legacy("8234", "DUP")
        self.legacy("8234", "DIFF")
        self.legacy("8235", "DIFF", pieces=6)
        migrate_rows(self.conn)
        migrate_rows(self.conn)
        graph = MaterialBOM(self.conn)
        self.assertEqual(len(graph.parents["KA"]), 2)
        self.assertEqual(graph.parts["KA"]["net_weight"], "0.25")
        self.assertEqual(len(graph.legacy), 4)
        self.assertEqual(len(graph.production()["8234"]), 4)
        self.assertEqual(len(graph.production()["8235"]), 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM material_items").fetchone()[0], 6)
        validate_graph(self.conn)

    def test_shared_stock_independent_yields_copy_and_assembly_explosion(self):
        for code in ("A", "B", "W"):
            self.create(code, code, "焊接件")
        self.create("KA8234", "主板", "冲压件", parent="A", spec="3.5*416*126", pieces=4)
        update_bom(self.conn, dict(action="attach", parent="B", code="KA8234", qty=2))
        update_bom(self.conn, dict(action="attach", parent="W", code="B", qty=3))
        update_bom(self.conn, dict(action="copy", source="KA8234", parent="A", code="KB", kind="冲压件",
                                   name="加强板", qty=1, spec="3.5*416*126", pieces=6))
        self.create("TG001", "焊接管", "焊接管", parent="B")
        graph = MaterialBOM(self.conn)
        self.assertEqual(len([p for p in graph.parts.values() if p["kind"] == "板料"]), 1)
        self.assertEqual([graph.parts[c]["pieces"] for c in ("KA8234", "KB")], [4, 6])
        rows = graph.production()
        plan = [dict(model="W", qty=100)]
        book = Workbook()
        sheet = book.active
        sheet.append(["测试生产计划"])
        sheet.append(PLAN_HEADERS)
        sheet.append(["W", "", 100])
        path = self.root / "plan.xlsx"
        book.save(path)
        book.close()
        output, _ = generate_material_sheet_from_materials(rows, path, self.root)
        book = load_workbook(output, read_only=True, data_only=True)
        try:
            row = next(book["料单明细"].iter_rows(min_row=2, values_only=True))
            self.assertEqual((row[2], row[5], row[8], row[9]), (600, "KA8234", 4, 150))
            tube = next(r for r in book["焊接子件需求"].iter_rows(min_row=2, values_only=True) if r[1] == "TG001")
            self.assertEqual(tuple(tube), ("W", "TG001", "焊接管", "焊接管", 3, 300))
        finally:
            book.close()
        update_bom(self.conn, dict(action="edit", code="KA8234", name="主板", spec="3.5*416*126", pieces=5))
        graph = MaterialBOM(self.conn)
        self.assertEqual(calculate(plan, graph.production())[0][0]["bar_qty"], 120)
        self.assertEqual(graph.parts["KB"]["pieces"], 6)
        self.assertEqual(len(graph.costing("KA8234")), 1)
        with self.assertRaisesRegex(ValueError, "循环"):
            update_bom(self.conn, dict(action="attach", parent="B", code="W", qty=1))
        with self.assertRaisesRegex(ValueError, "已有"):
            update_bom(self.conn, dict(action="attach", parent="B", code="KA8234", qty=1))
        update_bom(self.conn, dict(action="usage", parent="B", code="KA8234", qty=2, active="0"))
        self.assertEqual(calculate(plan, MaterialBOM(self.conn).production())[0], [])
        validate_graph(self.conn)

    def test_list_shows_each_direct_usage_and_filters_shared_stock(self):
        for code in ("A", "B"):
            self.create(code, code, "焊接件")
        self.create("K", "主板", "冲压件", parent="A", spec="3.5*416*126", pieces=4)
        update_bom(self.conn, dict(action="attach", parent="B", code="K", qty=2))
        update_bom(self.conn, dict(action="attach", parent="B", code="A", qty=1))
        update_bom(self.conn, dict(action="usage", parent="B", code="K", qty=2, active="0"))
        graph = MaterialBOM(self.conn)

        def rows(query, status):
            links = graph.page(query, status, 1, "list", "")["groups"]
            return [(r["parent"], r["child"], r["qty"]) for r in links]

        self.assertEqual(rows("", "all"), [("A", "K", 1), ("B", "A", 1), ("B", "K", 2)])
        self.assertEqual(rows("3.5*416*126", "active"), [("A", "K", 1)])
        self.assertEqual(rows("主板", "inactive"), [("B", "K", 2)])

    def test_copy_of_assembly_reuses_only_chosen_children(self):
        self.create("W", "焊接件", "焊接件")
        self.create("AX", "轴", "轴类", parent="W")
        for code, include in (("W2", "1"), ("W3", "")):
            update_bom(self.conn, dict(action="copy", source="W", code=code, kind="焊接件", name=code, copy_children=include))
        g = MaterialBOM(self.conn)
        self.assertEqual([r["child"] for r in g.children["W2"]], ["AX"])
        self.assertFalse(g.children["W3"])
        self.assertEqual(len(g.parents["AX"]), 2)

    def test_import_preview_is_nonmutating_and_retries_are_idempotent(self):
        self.create("W", "焊接件", "焊接件")
        self.create("AX", "轴", "轴类", parent="W")
        self.conn.commit()
        repo = SQLiteMaterialRepository(self.conn)
        path = self.root / "import.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "材料数据"
        sheet.append(["母件","子件","类别","车型","名称","规格","下料只数","重量","厚","宽","长"])
        sheet.append(["W","KA","","","主板","",4,"",3.5,416,126])
        book.save(path)
        preview = repo.preview_import(path)
        self.assertEqual(len(preview["new"]), 1)
        self.assertNotIn("KA", MaterialBOM(self.conn).parts)
        self.assertEqual(repo.import_data(path, actor="test", digest=preview["digest"]), 1)
        self.assertEqual(repo.import_data(path, actor="test"), 0)
        sheet.append(["W","KA","","","主板","",8,"",3.5,416,126])
        book.save(path)
        book.close()
        self.assertEqual(len(repo.preview_import(path)["conflicts"]), 1)
        with self.assertRaises(ValueError):
            repo.import_data(path, actor="test")
        self.assertEqual(len(MaterialBOM(self.conn).children["W"]), 2)

    def test_sync_roundtrip_preserves_shared_part_and_assembly_use(self):
        from app.modules.business_sync.infrastructure import BusinessSyncRepository
        self.create("W1", "焊接总成", "焊接件")
        self.create("W2", "焊接总成", "焊接件")
        self.create("KA8234", "主板", "冲压件", parent="W1", spec="3.5*416*126", pieces=4)
        update_bom(self.conn, dict(action="attach", parent="W2", code="KA8234", qty=2))
        self.conn.commit()
        source = BusinessSyncRepository(self.root / "test.sqlite3")
        target = BusinessSyncRepository(self.root / "target.sqlite3")
        package = source.export(output_path=self.root / "bom.tar.gz", selected=("materials",), actor="test")
        self.assertEqual(source.read(package)[0]["version"], 16)
        preview = target.preview(package)
        target.apply(package, backup_path=self.root/"backup.sqlite3", actor="test",
                     expected_token=preview["token"], selected_conflicts={})
        with connect(self.root / "target.sqlite3") as conn:
            graph = MaterialBOM(conn)
            self.assertEqual(len(graph.parts), 4)
            self.assertEqual(graph.production()["W2"][0]["assembly_qty"], 2)
            self.assertEqual(graph.parts["KA8234"]["pieces"], 4)

    def test_reviewed_duplicate_retains_both_sources_and_explicit_usage(self):
        self.legacy("W", "KA")
        self.legacy("W", "KA")
        migrate_rows(self.conn)
        self.assertEqual(len(MaterialBOM(self.conn).legacy), 2)
        update_bom(self.conn, dict(action="merge", parent="W", code="KA", qty=2))
        graph = MaterialBOM(self.conn)
        self.assertFalse(graph.legacy)
        self.assertEqual(graph.production()["W"][0]["assembly_qty"], 2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM material_items").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM bom_source_aliases").fetchone()[0], 1)
        validate_graph(self.conn)

class MaterialBOMWebTests(WebAppTestBase):
    def test_attach_existing_tube_and_copy_stamp_from_normal_forms(self):
        self.login()
        def save(**fields):
            return self.client.post("/materials/bom/save", data=fields, follow_redirects=True)
        save(action="create", code="W-BOM", kind="焊接件", name="试制总成")
        with self.web.connect(self.web.DB_PATH) as conn:
            conn.execute("""INSERT INTO tube_items(code,tube_type,spec_text,weight_kg,created_at,updated_at)
                VALUES ('TG-BOM','普通管','Ø35×30',0.15,'2026-01-01','2026-01-01')""")
        picker = self.client.get("/materials/items", query_string=dict(mode="attach", parent="W-BOM"))
        self.assertIn("TG-BOM", picker.get_data(as_text=True))
        save(action="attach", code="TG-BOM", tube_kind="焊接座管", parent="W-BOM", qty="2")
        tube = self.client.get("/materials/items", query_string=dict(mode="detail", selected="TG-BOM"))
        self.assertIn("Ø35×30", tube.get_data(as_text=True))
        save(action="create", code="KA-WEB", kind="冲压件", name="主板", spec="3.5×416×126",
             pieces="4", qty="1", parent="W-BOM")
        detail = self.client.get("/materials/items", query_string=dict(mode="detail", selected="KA-WEB"))
        self.assertIn("4 只/条", detail.get_data(as_text=True))
        self.assertNotIn('name="action" value="copy"', detail.get_data(as_text=True))
        save(action="copy", source="KA-WEB", code="KB-WEB", kind="冲压件", name="加强板",
             spec="3.5×416×126", pieces="6", qty="1", parent="W-BOM")
        duplicate = save(action="attach", code="KB-WEB", parent="W-BOM", qty="1")
        self.assertIn("修改装配关系", duplicate.get_data(as_text=True))
        with self.web.connect(self.web.DB_PATH) as conn:
            g = MaterialBOM(conn)
            self.assertEqual(len(g.children["W-BOM"]), 3)
            self.assertEqual(g.parts["KA-WEB"]["plate"], g.parts["KB-WEB"]["plate"])
            self.assertEqual(g.parts["TG-BOM"]["kind"], "焊接座管")
