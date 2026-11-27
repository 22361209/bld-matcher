from pathlib import Path
from tempfile import TemporaryDirectory
import io
import json
import tarfile
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
        values = dict(action="create", code=code, name=name, kind=kind, qty=1, **fields)
        if kind in ("焊接管", "焊接座管", "轴类"):
            self.conn.execute("""INSERT OR IGNORE INTO tube_items(code,tube_type,spec_text,weight_kg,created_at,updated_at)
                VALUES (?,?,'Ø35×30',0.15,'2026-01-01','2026-01-01')""", (code, kind))
            values["tube_code"] = code
        update_bom(self.conn, values)

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

    def test_tree_shows_each_direct_usage_and_filters_shared_stock(self):
        for code in ("A", "B"):
            self.create(code, code, "焊接件")
        self.create("K", "主板", "冲压件", parent="A", spec="3.5*416*126", pieces=4)
        update_bom(self.conn, dict(action="attach", parent="B", code="K", qty=2))
        update_bom(self.conn, dict(action="attach", parent="B", code="A", qty=1))
        update_bom(self.conn, dict(action="usage", parent="B", code="K", qty=2, active="0"))
        graph = MaterialBOM(self.conn)

        def rows(query, status):
            groups = graph.page(query, status, 1, "tree", "")["groups"]
            return [(parent["code"], child["code"], child["link"]["qty"]) for parent in groups for child in parent["nodes"]]

        self.assertEqual(rows("", "all"), [("A", "K", 1), ("B", "A", 1), ("B", "K", 2)])
        self.assertEqual(rows("3.5*416*126", "active"), [("A", "K", 1), ("B", "A", 1)])
        self.assertEqual(rows("主板", "inactive"), [("B", "K", 2)])

    def test_library_groups_by_kind_and_filters_every_display_value(self):
        self.create("Z-ASSEMBLY", "总成", "焊接件")
        self.create("Z-STAMP", "主板", "冲压件", spec="3.5*416*126", pieces=4)
        self.create("A-TUBE", "焊接管", "焊接管")
        page = MaterialBOM(self.conn).page("", "active", 1, "library", "")
        self.assertEqual([p["code"] for p in page["groups"]],
                         ["Z-STAMP", "A-TUBE", "SPHC/3.5×416×126"])
        self.assertEqual(set(page["column_filter_options"]),
                         {"code", "name", "model", "kind", "grade", "spec", "pieces", "gross_weight",
                          "net_weight", "reference_count", "actions"})
        self.assertEqual(page["groups"][0]["gross_weight"], "0.3600324")
        self.assertNotIn("焊接件", [option["value"] for option in page["column_filter_options"]["kind"]])
        code_options = [option["value"] for option in page["column_filter_options"]["code"]]
        self.assertIn("SPHC/3.5×416×126", code_options)
        self.assertNotIn("板料/SPHC/3.5×416×126", code_options)
        filtered = MaterialBOM(self.conn).page("", "active", 1, "library", "", {"cf_kind": ["冲压件"]})
        self.assertEqual([p["code"] for p in filtered["groups"]], ["Z-STAMP"])
        named = MaterialBOM(self.conn).page("", "active", 1, "library", "", {"cf_name": ["焊接管"]})
        self.assertEqual([p["code"] for p in named["groups"]], ["A-TUBE"])
        for key, value, expected in (("grade", "SPHC", ["Z-STAMP", "SPHC/3.5×416×126"]),
                                     ("spec", "3.5×416×126", ["Z-STAMP", "SPHC/3.5×416×126"]),
                                     ("pieces", "4", ["Z-STAMP"]),
                                     ("gross_weight", "0.3600", ["Z-STAMP"])):
            with self.subTest(column=key):
                filtered = MaterialBOM(self.conn).page("", "active", 1, "library", "", {f"cf_{key}": [value]})
                self.assertEqual([p["code"] for p in filtered["groups"]], expected)
        plate = MaterialBOM(self.conn).page("", "active", 1, "library", "", {"cf_code": ["SPHC/3.5×416×126"]})
        self.assertEqual([p["code"] for p in plate["groups"]], ["SPHC/3.5×416×126"])

    def test_plate_code_migration_preserves_references_and_rejects_collisions(self):
        from app.modules.materials.plate_codes import migrate_plate_codes

        self.create("W", "总成", "焊接件")
        self.create("Z-STAMP", "主板", "冲压件", parent="W", spec="3.5*416*126", pieces=4)
        code = "SPHC/3.5×416×126"
        old = f"板料/{code}"
        updated_at = self.conn.execute("SELECT updated_at FROM bom_parts WHERE code=?", (code,)).fetchone()[0]
        self.conn.execute("UPDATE bom_parts SET plate=? WHERE plate=?", (old, code))
        self.conn.execute("UPDATE bom_parts SET code=? WHERE code=?", (old, code))
        self.assertEqual(migrate_plate_codes(self.conn), 1)
        graph = MaterialBOM(self.conn)
        self.assertNotIn(old, graph.parts)
        self.assertEqual(graph.parts["Z-STAMP"]["plate"], code)
        self.assertEqual(graph.parts[code]["updated_at"], updated_at)
        self.assertEqual(graph.costing("Z-STAMP")[0]["gross_weight"], "0.3600324")
        validate_graph(self.conn)

        self.conn.execute("UPDATE bom_parts SET plate=? WHERE plate=?", (old, code))
        self.conn.execute("UPDATE bom_parts SET code=? WHERE code=?", (old, code))
        self.conn.execute("INSERT INTO bom_parts(code,name,kind,updated_at) VALUES (?,'冲突件','焊接件','2026-01-01')", (code,))
        with self.assertRaisesRegex(ValueError, "已被其他物料使用"):
            migrate_plate_codes(self.conn)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM bom_parts WHERE code=?", (old,)).fetchone())

    def test_new_tube_part_requires_registered_tube_code(self):
        self.create("W", "焊接件", "焊接件")
        with self.assertRaisesRegex(ValueError, "管件资料"):
            update_bom(self.conn, dict(action="create", parent="W", code="MISSING", kind="焊接管", name="管件", qty=1))
        self.assertNotIn("MISSING", MaterialBOM(self.conn).parts)

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
        self.create("W1", "焊接总成", "焊接件", model="MODEL-1")
        self.create("W2", "焊接总成", "焊接件")
        self.create("KA8234", "主板", "冲压件", parent="W1", spec="3.5*416*126", pieces=4)
        update_bom(self.conn, dict(action="attach", parent="W2", code="KA8234", qty=2))
        self.conn.commit()
        source = BusinessSyncRepository(self.root / "test.sqlite3")
        target = BusinessSyncRepository(self.root / "target.sqlite3")
        package = source.export(output_path=self.root / "bom.tar.gz", selected=("materials",), actor="test")
        self.assertEqual(source.read(package)[0]["version"], 19)
        preview = target.preview(package)
        target.apply(package, backup_path=self.root/"backup.sqlite3", actor="test",
                     expected_token=preview["token"], selected_conflicts={})
        with connect(self.root / "target.sqlite3") as conn:
            graph = MaterialBOM(conn)
            self.assertEqual(len(graph.parts), 4)
            self.assertEqual(graph.production()["W2"][0]["assembly_qty"], 2)
            self.assertEqual(graph.parts["KA8234"]["pieces"], 4)
            self.assertEqual(graph.parts["W1"]["model"], "MODEL-1")
        update_bom(self.conn, dict(action="remove", parent="W2", code="KA8234"), actor="tester")
        self.conn.commit()
        removed_package = source.export(output_path=self.root / "bom-removed.tar.gz", selected=("materials",), actor="test")
        preview = target.preview(removed_package)
        target.apply(removed_package, backup_path=self.root/"backup-removed.sqlite3", actor="test",
                     expected_token=preview["token"], selected_conflicts={})
        with connect(self.root / "target.sqlite3") as conn:
            self.assertFalse(MaterialBOM(conn).production().get("W2"))
            self.assertEqual(conn.execute("SELECT count(*) FROM bom_removed_sources").fetchone()[0], 0)
        update_bom(self.conn, dict(action="restore", parent="W2", code="KA8234"))
        self.conn.commit()
        restored_package = source.export(output_path=self.root / "bom-restored.tar.gz", selected=("materials",), actor="test")
        preview = target.preview(restored_package)
        target.apply(restored_package, backup_path=self.root/"backup-restored.sqlite3", actor="test",
                     expected_token=preview["token"], selected_conflicts={})
        with connect(self.root / "target.sqlite3") as conn:
            self.assertEqual(MaterialBOM(conn).production()["W2"][0]["assembly_qty"], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM bom_removed_links").fetchone()[0], 0)

    def test_v16_packages_remain_compatible_without_overriding_a_removal(self):
        from app.modules.business_sync.infrastructure import BusinessSyncRepository
        self.create("W", "焊接总成", "焊接件")
        self.create("KA8234", "主板", "冲压件", parent="W", spec="3.5*416*126", pieces=4)
        self.conn.commit()
        source = BusinessSyncRepository(self.root / "test.sqlite3")
        target = BusinessSyncRepository(self.root / "target.sqlite3")
        current = source.export(output_path=self.root / "current.tar.gz", selected=("materials",), actor="test")
        manifest, payload = source.read(current)
        manifest["version"] = 16
        manifest["datasets"] = [key for key in manifest["datasets"] if not key.startswith("bom_removed_")]
        for key in ("bom_removed_links", "bom_removed_sources"):
            payload.pop(key)
        for row in payload["bom_parts"]:
            row.pop("model")
            if row["kind"] == "板料":
                row["code"] = f'板料/{row["code"]}'
            if row.get("plate"):
                row["plate"] = f'板料/{row["plate"]}'
        legacy = self.root / "v16.tar.gz"
        with tarfile.open(legacy, "w:gz") as archive:
            for name, value in (("manifest.json", manifest), ("data.json", payload)):
                encoded = json.dumps(value, ensure_ascii=False).encode()
                member = tarfile.TarInfo(name)
                member.size = len(encoded)
                archive.addfile(member, io.BytesIO(encoded))
        preview = target.preview(legacy)
        target.apply(legacy, backup_path=self.root/"backup-v16.sqlite3", actor="test",
                     expected_token=preview["token"], selected_conflicts={})
        with connect(self.root / "target.sqlite3") as conn:
            graph = MaterialBOM(conn)
            self.assertEqual(graph.production()["W"][0]["assembly_qty"], 1)
            self.assertIn("SPHC/3.5×416×126", graph.parts)
            self.assertNotIn("板料/SPHC/3.5×416×126", graph.parts)
            self.assertEqual(graph.parts["KA8234"]["plate"], "SPHC/3.5×416×126")
        with connect(self.root / "target.sqlite3") as conn:
            update_bom(conn, dict(action="remove", parent="W", code="KA8234"), actor="tester")
            conn.commit()
        preview = target.preview(legacy)
        with self.assertRaisesRegex(ValueError, "已移除"):
            target.apply(legacy, backup_path=self.root/"backup-v16-removed.sqlite3", actor="test",
                         expected_token=preview["token"], selected_conflicts={})

    def test_removed_legacy_relation_stays_out_of_production_until_restored(self):
        self.legacy("W", "KA")
        migrate_rows(self.conn)
        update_bom(self.conn, dict(action="remove", parent="W", code="KA"), actor="tester")
        migrate_rows(self.conn)
        graph = MaterialBOM(self.conn)
        self.assertFalse(graph.production().get("W"))
        self.assertFalse(graph.legacy)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM bom_removed_sources").fetchone()[0], 1)
        self.assertEqual(graph.page("", "removed", 1, "tree", "")["total"], 1)
        validate_graph(self.conn)
        update_bom(self.conn, dict(action="restore", parent="W", code="KA"))
        graph = MaterialBOM(self.conn)
        self.assertEqual(graph.production()["W"][0]["assembly_qty"], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM bom_removed_links").fetchone()[0], 0)

    def test_restore_rechecks_cycles_introduced_after_removal(self):
        self.create("A", "总成 A", "焊接件")
        self.create("B", "总成 B", "焊接件")
        update_bom(self.conn, dict(action="attach", parent="A", code="B", qty=1))
        update_bom(self.conn, dict(action="remove", parent="A", code="B"))
        update_bom(self.conn, dict(action="attach", parent="B", code="A", qty=1))
        with self.assertRaisesRegex(ValueError, "循环"):
            update_bom(self.conn, dict(action="restore", parent="A", code="B"))

    def test_delete_detail_cascades_references_history_and_removed_archives(self):
        self.create("FREE", "独立物料", "焊接件")
        update_bom(self.conn, dict(action="delete", code="FREE"))
        self.assertNotIn("FREE", MaterialBOM(self.conn).parts)
        self.legacy("W", "KA8234")
        migrate_rows(self.conn)
        plate = "SPHC/3.5×416×126"
        with self.assertRaisesRegex(ValueError, "板料.*引用"):
            update_bom(self.conn, dict(action="delete", code=plate))
        update_bom(self.conn, dict(action="delete", code="KA8234"))
        graph = MaterialBOM(self.conn)
        self.assertIn("W", graph.parts)
        self.assertNotIn("KA8234", graph.parts)
        self.assertFalse(graph.children["W"])
        self.assertFalse(graph.legacy)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM material_items WHERE code='KA8234'").fetchone()[0], 0)

        self.create("KA8234", "主板", "冲压件", parent="W", spec="3.5*416*126", pieces=4)
        update_bom(self.conn, dict(action="remove", parent="W", code="KA8234"))
        update_bom(self.conn, dict(action="delete", code="KA8234"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM bom_removed_links WHERE child='KA8234'").fetchone()[0], 0)
        update_bom(self.conn, dict(action="delete", code=plate))
        self.assertNotIn(plate, MaterialBOM(self.conn).parts)

    def test_delete_assembly_cascades_outgoing_structure_and_preserves_children(self):
        self.legacy("W-DELETE", "KA-LEGACY")
        migrate_rows(self.conn)
        self.create("KA-NEW", "新主板", "冲压件", parent="W-DELETE", spec="3.5*416*126", pieces=4)
        self.create("TOP", "上级总成", "焊接件")
        update_bom(self.conn, dict(action="attach", parent="TOP", code="W-DELETE", qty=1))
        with self.assertRaisesRegex(ValueError, "引用"):
            update_bom(self.conn, dict(action="delete", code="W-DELETE"))
        update_bom(self.conn, dict(action="remove", parent="TOP", code="W-DELETE"))
        with self.assertRaisesRegex(ValueError, "引用"):
            update_bom(self.conn, dict(action="delete", code="W-DELETE"))
        update_bom(self.conn, dict(action="delete", code="TOP"))
        update_bom(self.conn, dict(action="remove", parent="W-DELETE", code="KA-NEW"))
        self.assertTrue(MaterialBOM(self.conn).parts["W-DELETE"]["deletable"])
        update_bom(self.conn, dict(action="delete", code="W-DELETE"))
        graph = MaterialBOM(self.conn)
        self.assertNotIn("W-DELETE", graph.parts)
        self.assertIn("KA-LEGACY", graph.parts)
        self.assertIn("KA-NEW", graph.parts)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM material_items WHERE model='W-DELETE'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM bom_links WHERE parent='W-DELETE'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM bom_removed_links WHERE parent='W-DELETE'").fetchone()[0], 0)
        validate_graph(self.conn)

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
        self.assertIn("装配用量", duplicate.get_data(as_text=True))
        tree = self.client.get("/materials/items", query_string=dict(q="W-BOM")).get_data(as_text=True)
        self.assertIn("bom-tree-table", tree)
        self.assertIn('data-bom-toggle="W-BOM"', tree)
        self.assertNotIn('name="action" value="remove"', tree)
        self.assertNotIn(">添加</a>", tree)
        self.assertIn("新增母件", tree)
        usage = self.client.get("/materials/items", query_string=dict(mode="usage", parent="W-BOM", selected="KB-WEB"))
        self.assertIn('name="action" value="remove"', usage.get_data(as_text=True))
        self.assertNotIn("bom-branch", tree)
        with self.web.connect(self.web.DB_PATH) as conn:
            g = MaterialBOM(conn)
            self.assertEqual(len(g.children["W-BOM"]), 3)
            self.assertEqual(g.parts["KA-WEB"]["plate"], g.parts["KB-WEB"]["plate"])
            self.assertEqual(g.parts["TG-BOM"]["kind"], "焊接座管")
            revision = next(link["updated_at"] for link in g.children["W-BOM"] if link["child"] == "KB-WEB")
        removed = save(action="remove", parent="W-BOM", code="KB-WEB", revision=revision)
        self.assertIn("装配关系已移除", removed.get_data(as_text=True))
        removed_page = self.client.get("/materials/items", query_string=dict(view="list", status="removed")).get_data(as_text=True)
        self.assertIn("KB-WEB", removed_page)
        self.assertIn('name="action" value="restore"', removed_page)
        legacy_list = self.client.get("/materials/items", query_string=dict(view="list"))
        self.assertEqual(legacy_list.status_code, 200)
        self.assertNotIn("明细列表", legacy_list.get_data(as_text=True))

    def test_assembly_workflow_and_library_column_controls(self):
        self.login()
        form = self.client.get("/materials/items", query_string=dict(mode="assembly")).get_data(as_text=True)
        self.assertIn('name="action" value="assembly_save"', form)
        self.assertIn('name="model"', form)
        self.assertIn("data-bom-child-rows", form)
        self.assertIn('<col class="bom-sequence-column"><col class="bom-child-code-column">', form)
        styles = (Path(__file__).parents[1] / "static/pages/material_bom.css").read_text()
        self.assertIn("grid-template-columns:minmax(0,1.15fr) minmax(0,1.3fr) minmax(0,1fr) minmax(0,.75fr)", styles)
        self.assertIn(".bom-assembly-table {width:100%;min-width:600px;table-layout:fixed}", styles)
        self.assertIn(".bom-sequence-column {width:52px}", styles)
        self.assertIn(".bom-quantity-column {width:88px}", styles)
        created = self.client.post("/materials/bom/save", data=dict(
            action="assembly_save", mode="assembly", source_view="tree", code="W-FIRST", name="先建母件",
            model="MODEL-1", child_code=["KA-FIRST"], child_qty=["2"], new_name=["主板"],
            new_kind=["冲压件"], new_spec=["3.5×416×126"], new_pieces=["4"], new_net_weight=[""]
        ), follow_redirects=True)
        detail = created.get_data(as_text=True)
        self.assertIn("母件及子件清单已保存", detail)
        self.assertIn("编辑母件及子件", detail)
        self.assertIn("MODEL-1", detail)
        self.assertIn("KA-FIRST", detail)
        invalid = self.client.post("/materials/bom/save", data=dict(
            action="assembly_save", mode="assembly", code="W-TUBE", name="管件母件",
            child_code=["NOT-REGISTERED"], child_qty=["1"], new_name=["管件"], new_kind=["焊接管"]
        ), follow_redirects=True)
        self.assertIn("请先建立管件资料", invalid.get_data(as_text=True))
        library = self.client.get("/materials/items", query_string=dict(view="library")).get_data(as_text=True)
        self.assertIn(">材料明细</a>", library)
        self.assertNotIn(">物料档案</a>", library)
        self.assertNotIn("W-FIRST", library)
        self.assertIn("KA-FIRST", library)
        self.assertEqual(library.count("data-column-filter-panel"), 11)
        self.assertEqual(library.count("<col data-col="), 11)
        for label in ("材料牌号", "规格 mm", "下料数量", "毛重 kg"):
            self.assertIn(f'data-column-label="{label}"', library)
        self.assertIn('data-col="gross_weight" class="bom-number">0.3600</td>', library)
        self.assertIn(">SPHC/3.5×416×126</a>", library)
        self.assertNotIn(">板料/SPHC/3.5×416×126</a>", library)
        self.assertIn('data-filter-key="cf_code"', library)
        self.assertIn('value="SPHC/3.5×416×126" checked', library)
        legacy_detail = self.client.get("/materials/items", query_string=dict(mode="detail", selected="板料/SPHC/3.5×416×126"))
        self.assertEqual(legacy_detail.status_code, 200)
        self.assertIn("<h3>SPHC/3.5×416×126　SPHC</h3>", legacy_detail.get_data(as_text=True))
        filtered = self.client.get("/materials/items", query_string=dict(view="library", cf_kind="焊接件"))
        page = filtered.get_data(as_text=True)
        self.assertNotIn("W-FIRST", page)
        self.assertIn('<td colspan="11">没有找到符合条件的物料。</td>', page)
        self.assertIn('name="cf_kind" value="焊接件"', page)
        self.assertIn("cf_kind=%E7%84%8A%E6%8E%A5%E4%BB%B6", page)

    def test_library_shows_delete_for_referenced_and_unreferenced_details(self):
        self.login()
        def save(**fields):
            return self.client.post("/materials/bom/save", data=fields, follow_redirects=True)

        save(action="assembly_save", mode="assembly", code="W-LIST", name="列表母件",
             child_code=["KA-LIST"], child_qty=["1"], new_name=["引用件"], new_kind=["冲压件"],
             new_spec=["3.5×416×126"], new_pieces=["4"])
        save(action="create", mode="create", code="FREE-LIST", name="独立件", kind="冲压件",
             spec="3.5×416×126", pieces="4")
        with self.web.connect(self.web.DB_PATH) as conn:
            revision = MaterialBOM(conn).parts["FREE-LIST"]["updated_at"]
        library = self.client.get("/materials/items", query_string=dict(view="library")).get_data(as_text=True)
        self.assertNotIn("W-LIST", library)
        self.assertIn("KA-LIST", library)
        self.assertIn("FREE-LIST", library)
        self.assertIn('name="code" value="KA-LIST"', library)
        self.assertIn('name="code" value="FREE-LIST"', library)
        self.assertIn("其 BOM 引用和对应历史明细将一并删除，母件及管件主档保留", library)
        deleted = save(action="delete", code="FREE-LIST", revision=revision)
        page = deleted.get_data(as_text=True)
        self.assertIn("材料明细已删除", page)
        self.assertNotIn("FREE-LIST", page)
        self.assertIn("KA-LIST", page)
        with self.web.connect(self.web.DB_PATH) as conn:
            graph = MaterialBOM(conn)
            self.assertNotIn("FREE-LIST", graph.parts)
            revision = graph.parts["W-LIST"]["updated_at"]
        detail = self.client.get("/materials/items", query_string=dict(mode="detail", selected="W-LIST")).get_data(as_text=True)
        self.assertIn("删除母件", detail)
        self.assertIn("子件档案保留", detail)
        deleted = save(action="delete", code="W-LIST", revision=revision)
        self.assertIn("材料明细已删除", deleted.get_data(as_text=True))
        with self.web.connect(self.web.DB_PATH) as conn:
            graph = MaterialBOM(conn)
            self.assertNotIn("W-LIST", graph.parts)
            self.assertIn("KA-LIST", graph.parts)
            self.assertFalse(graph.children["W-LIST"])

    def test_library_delete_removes_referenced_detail_and_preserves_parent(self):
        self.login()
        created = self.client.post("/materials/bom/save", data=dict(
            action="assembly_save", mode="assembly", code="W-REFERENCED", name="保留母件",
            child_code=["KA-REFERENCED"], child_qty=["2"], new_name=["待删除引用件"], new_kind=["冲压件"],
            new_spec=["3.5×416×126"], new_pieces=["4"]
        ), follow_redirects=True)
        self.assertEqual(created.status_code, 200)
        with self.web.connect(self.web.DB_PATH) as conn:
            revision = MaterialBOM(conn).parts["KA-REFERENCED"]["updated_at"]
        deleted = self.client.post("/materials/bom/save", data=dict(
            action="delete", code="KA-REFERENCED", revision=revision
        ), follow_redirects=True)
        self.assertIn("材料明细已删除", deleted.get_data(as_text=True))
        with self.web.connect(self.web.DB_PATH) as conn:
            graph = MaterialBOM(conn)
            self.assertIn("W-REFERENCED", graph.parts)
            self.assertNotIn("KA-REFERENCED", graph.parts)
            self.assertFalse(graph.children["W-REFERENCED"])
