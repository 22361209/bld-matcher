from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from app.database import connect
from app.modules.materials import excel_import, item_store, persistence


def material_workflow_snapshot(root: Path) -> dict[str, Any]:
    database_path = root / "materials.sqlite3"
    workbook_path = root / "materials.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "材料数据"
    sheet.append(
        [
            "型号",
            "编码",
            "类别",
            "车型",
            "零件名称",
            "规格尺寸",
            "下料只数",
            "单重",
            "厚度",
            "宽度",
            "长度",
        ]
    )
    sheet.append(["MAT-IMPORT", "IMP-001", "冲压", "测试车型", "导入件", "旧规格", 4, "", 3.2, 250, 900])
    book.save(workbook_path)
    book.close()

    with connect(database_path) as connection:
        first_id = item_store.upsert_material_item(
            connection,
            {
                "model": "MAT-001",
                "code": "PART-001",
                "category": "冲压",
                "car": "车型A",
                "part": "左支架",
                "spec_text": "2.5 x 357 x 1260",
                "pieces": "3",
                "active": "1",
            },
            actor="baseline",
        )
        second_id = item_store.upsert_material_item(
            connection,
            {
                "model": "MAT-002",
                "code": "PART-002",
                "category": "锻造",
                "car": "车型B",
                "part": "右支架",
                "thickness": "4",
                "width": "92.5",
                "length": "1260",
                "pieces": "2",
                "active": "1",
            },
            actor="baseline",
        )
        import_count = excel_import.import_materials_from_excel(
            connection,
            workbook_path,
            replace=False,
            actor="baseline",
        )
        before_deactivate = [
            dict(row)
            for row in item_store.list_material_items(
                connection,
                include_inactive=True,
            )
        ]
        search_counts = {
            query: item_store.count_material_items(
                connection,
                query=query,
                include_inactive=True,
            )
            for query in ("MAT-001", "2.5 357", "92.5×1260", "3.2")
        }
        item_store.deactivate_material_item(connection, second_id, actor="baseline")
        return {
            "ids": [first_id, second_id],
            "import_count": import_count,
            "schema_sql": connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='material_items'"
            ).fetchone()[0],
            "columns": [dict(row) for row in connection.execute("PRAGMA table_info(material_items)")],
            "before_deactivate": before_deactivate,
            "first": dict(item_store.get_material_item(connection, first_id)),
            "search_counts": search_counts,
            "active_models": item_store.rows_for_material_sheet(connection),
            "stats": item_store.material_item_stats(connection),
            "inactive_count": item_store.count_material_items(connection, only_inactive=True),
            "ordered_ids": [
                row["id"]
                for row in item_store.list_material_items(
                    connection,
                    include_inactive=True,
                )
            ],
        }


class MaterialPersistenceModuleTest(unittest.TestCase):
    def test_compatibility_facade_keeps_public_entrypoints(self) -> None:
        self.assertIs(persistence.import_materials_from_excel, excel_import.import_materials_from_excel)
        self.assertIs(persistence.bootstrap_materials_from_excel, excel_import.bootstrap_materials_from_excel)
        self.assertIs(persistence.upsert_material_item, item_store.upsert_material_item)
        self.assertIs(persistence.list_material_items, item_store.list_material_items)

    def test_material_workflow_and_schema_remain_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            snapshot = material_workflow_snapshot(Path(temporary_dir))

        self.assertEqual(
            [column["name"] for column in snapshot["columns"]],
            [
                "id",
                "model",
                "code",
                "category",
                "car",
                "part",
                "spec_text",
                "pieces",
                "thickness",
                "width",
                "length",
                "active",
                "source",
                "source_row",
                "created_at",
                "updated_at",
            ],
        )
        self.assertEqual(snapshot["import_count"], 1)
        self.assertEqual(snapshot["search_counts"], {"MAT-001": 1, "2.5 357": 1, "92.5×1260": 1, "3.2": 1})
        self.assertEqual(
            [row["spec_text"] for row in snapshot["before_deactivate"]],
            ["2.5×357×1260", "4.0×92.5×1260", "3.2×250×900"],
        )
        self.assertEqual(snapshot["stats"], {"items": 3, "active": 2, "inactive": 1, "models": 2})
        self.assertEqual(snapshot["inactive_count"], 1)


if __name__ == "__main__":
    unittest.main()
