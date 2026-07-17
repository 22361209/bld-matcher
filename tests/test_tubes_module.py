from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.database import connect
from app.modules.tubes.domain import row_from_2026, spec_display_lines, tolerance_only
from app.modules.tubes.repository import TubeRepository
from app.modules.tubes.service import TubeService


class TubeModuleTests(unittest.TestCase):
    def test_2026_row_classifies_borrowed_double_flange_and_converts_mm(self) -> None:
        values = [
            44,
            None,
            "8038（8036）",
            "KE8038（8036）",
            "ø35×30×40（法兰后）×47（法兰径）",
            "ø35×30",
            "30-0.2",
            "52.5+0.25",
            "52±0.1",
            "焊接管",
            "双边法兰",
            "借用8036",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ]
        row = row_from_2026(values, 46)

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.code, "KE8038")
        self.assertEqual(row.tube_type, "双法兰管")
        self.assertEqual(row.borrowed_from, "KE8036")
        self.assertIsNone(row.weight_kg)

    def test_2026_row_keeps_weight_and_millimetre_inputs(self) -> None:
        values = [
            1,
            None,
            "8001",
            "KE8001",
            "ø41×34.5×41",
            "ø41×34.5",
            "34.5-0.2",
            "41.5+0.25",
            "41±0.1",
            "焊接管",
            None,
            None,
            41,
            34.5,
            0.0415,
            0.00025,
            0.003,
            1,
            0.1353899503125,
        ]
        row = row_from_2026(values, 9)

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.tube_type, "普通管")
        self.assertAlmostEqual(row.tolerance_mm or 0, 0.25)
        self.assertAlmostEqual(row.consumption_mm or 0, 3.0)
        self.assertAlmostEqual(row.weight_kg or 0, 0.1353899503125)

    def test_spec_display_and_search_normalize_flange_notation(self) -> None:
        self.assertEqual(
            spec_display_lines("ø35×30×40（法兰后）×47（法兰径）"),
            ("ø35×30×40", "×47（法兰径）"),
        )

    def test_2026_note_extracts_purchase_base(self) -> None:
        values = [None] * 19
        values[3] = "KE9000"
        values[4] = "ø35×30×40"
        values[6] = "30±0.1"
        values[8] = "40±0.1"
        values[11] = "一只产品用2个管"
        row = row_from_2026(values, 1)

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.blank_length_text, "40±0.1")
        self.assertEqual(row.inner_diameter_tolerance, "30±0.1")
        self.assertEqual(row.purchase_base, 2)

    def test_tolerance_only_removes_inner_diameter(self) -> None:
        self.assertEqual(tolerance_only("30±0.1"), "±0.1")
        self.assertEqual(tolerance_only("33-0.2"), "-0.2")
        self.assertEqual(tolerance_only("33-0.1/0.2"), "-0.1\n-0.2")

    def test_repository_filters_type_and_searches_borrowed_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tubes.sqlite3"
            with connect(path) as connection:
                repository = TubeRepository(connection)
                repository.save(
                    {"code": "KE8036", "tube_type": "双法兰管", "spec_text": "ø35×30×40（法兰后）×47（法兰径）", "weight_kg": "0.5", "outer_diameter_mm": "35", "inner_diameter_mm": "30", "inner_diameter_tolerance": "30±0.2"},
                    actor="tester",
                )
                repository.save(
                    {"code": "KE-DOUBLE", "tube_type": "双法兰管", "spec_text": "ø35×30×40（法兰后）×47（法兰径）", "borrowed_from": "KE8036", "weight_kg": "0.5", "outer_diameter_mm": "35", "inner_diameter_mm": "30"},
                    actor="tester",
                )
                repository.save(
                    {"code": "KP-AXLE", "tube_type": "拉杆轴", "spec_text": "ø22×17"},
                    actor="tester",
                )
                repository.save(
                    {"code": "KE-FILTER", "tube_type": "普通管", "spec_text": "ø40×32", "blank_length_text": "45±0.1", "inner_diameter_tolerance": "32±0.2", "purchase_base": "2"},
                    actor="tester",
                )
                repository.save(
                    {"code": "KE-SLASH", "tube_type": "普通管", "spec_text": "ø38×28", "inner_diameter_tolerance": "28-0.1/0.2"},
                    actor="tester",
                )
                connection.commit()

                rows = repository.list(filters={"query": "8036"}, limit=20, offset=0)
                self.assertEqual([row["code"] for row in rows], ["KE8036"])
                self.assertEqual(rows[0]["borrowed_codes"], "KE-DOUBLE")
                rows = repository.list(filters={"query": "KE-DOUBLE"}, limit=20, offset=0)
                self.assertEqual([row["code"] for row in rows], ["KE8036"])
                rows = repository.list(filters={"query": "35*30*40"}, limit=20, offset=0)
                self.assertEqual([row["code"] for row in rows], ["KE8036"])
                self.assertEqual(repository.count(filters={"tube_types": ("拉杆轴",)}), 1)
                self.assertEqual(repository.type_counts()["双法兰管"], 1)
                self.assertEqual(repository.count(filters={"outer_diameter": 35.0, "inner_diameter": 30.0}), 1)
                self.assertEqual(repository.count(filters={"weight_min": 0.4, "weight_max": 0.6}), 1)
                self.assertEqual(repository.count(filters={"blank_lengths": ("45±0.1",)}), 1)
                self.assertEqual(repository.count(filters={"inner_tolerances": ("32±0.2",)}), 1)
                self.assertEqual(repository.count(filters={"purchase_bases": ("2",)}), 1)
                self.assertEqual(repository.value_counts("blank_length_text"), [{"value": "45±0.1", "label": "45±0.1", "count": 1}])
                @contextmanager
                def unit_of_work():
                    yield SimpleNamespace(repository=repository)

                service = TubeService(unit_of_work)
                filtered = service.list_items(filters={"inner_tolerances": ("±0.2",)}, limit=20, offset=0)
                self.assertEqual([row["code"] for row in filtered["records"]], ["KE8036", "KE-FILTER"])
                self.assertEqual(
                    filtered["inner_tolerance_options"],
                    [
                        {"value": "-0.1\n-0.2", "label": "-0.1\n-0.2", "count": 1},
                        {"value": "±0.2", "label": "±0.2", "count": 2},
                    ],
                )

    def test_source_item_can_manage_borrowed_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tubes.sqlite3"
            with connect(path) as connection:
                repository = TubeRepository(connection)
                source_id = repository.save(
                    {"code": "KE8036", "tube_type": "双法兰管", "spec_text": "ø35×30×40"},
                    actor="tester",
                )
                repository.save(
                    {"id": source_id, "code": "KE8036", "tube_type": "双法兰管", "spec_text": "ø35×30×40", "borrowed_codes": "KE8038\nKE8043"},
                    actor="tester",
                )
                self.assertEqual(repository.get(source_id)["borrowed_codes"], "KE8038\nKE8043")
                repository.save(
                    {"id": source_id, "code": "KE8036", "tube_type": "双法兰管", "spec_text": "ø35×30×40", "borrowed_codes": "KE8043\nKE8059"},
                    actor="tester",
                )
                rows = {row["code"]: row["borrowed_from"] for row in connection.execute("SELECT code, borrowed_from FROM tube_items ORDER BY code")}
                self.assertEqual(rows["KE8038"], "")
                self.assertEqual(rows["KE8043"], "KE8036")
                self.assertEqual(rows["KE8059"], "KE8036")

    def test_borrowing_resolves_to_root_and_rejects_self_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tubes.sqlite3"
            with connect(path) as connection:
                repository = TubeRepository(connection)
                root_id = repository.save({"code": "KE-ROOT", "tube_type": "普通管", "spec_text": "ø35×30"}, actor="tester")
                repository.save({"code": "KE-MID", "tube_type": "普通管", "spec_text": "ø35×30", "borrowed_from": "KE-ROOT"}, actor="tester")
                repository.save({"code": "KE-CHILD", "tube_type": "普通管", "spec_text": "ø35×30", "borrowed_from": "KE-MID"}, actor="tester")
                self.assertEqual(connection.execute("SELECT borrowed_from FROM tube_items WHERE code = 'KE-CHILD'").fetchone()[0], "KE-ROOT")
                with self.assertRaisesRegex(ValueError, "不能借用自身"):
                    repository.save({"id": root_id, "code": "KE-ROOT", "tube_type": "普通管", "spec_text": "ø35×30", "borrowed_from": "KE-ROOT"}, actor="tester")

    def test_source_item_flattens_existing_borrowing_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tubes.sqlite3"
            with connect(path) as connection:
                repository = TubeRepository(connection)
                root_id = repository.save({"code": "KE-ROOT", "tube_type": "普通管", "spec_text": "ø35×30"}, actor="tester")
                repository.save({"code": "KE-MID", "tube_type": "普通管", "spec_text": "ø35×30"}, actor="tester")
                repository.save({"code": "KE-CHILD", "tube_type": "普通管", "spec_text": "ø35×30", "borrowed_from": "KE-MID"}, actor="tester")
                repository.save({"id": root_id, "code": "KE-ROOT", "tube_type": "普通管", "spec_text": "ø35×30", "borrowed_codes": "KE-MID"}, actor="tester")
                rows = {row["code"]: row["borrowed_from"] for row in connection.execute("SELECT code, borrowed_from FROM tube_items ORDER BY code")}
                self.assertEqual(rows["KE-MID"], "KE-ROOT")
                self.assertEqual(rows["KE-CHILD"], "KE-ROOT")

    def test_migration_flattens_historical_borrowing_tree(self) -> None:
        from app.migrations import _flatten_tube_borrowing

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tubes.sqlite3"
            with connect(path) as connection:
                repository = TubeRepository(connection)
                repository.save({"code": "KE-ROOT", "tube_type": "普通管", "spec_text": "ø35×30"}, actor="tester")
                repository.save({"code": "KE-MID", "tube_type": "普通管", "spec_text": "ø35×30"}, actor="tester")
                repository.save({"code": "KE-CHILD", "tube_type": "普通管", "spec_text": "ø35×30"}, actor="tester")
                connection.execute("UPDATE tube_items SET borrowed_from = 'KE-ROOT' WHERE code = 'KE-MID'")
                connection.execute("UPDATE tube_items SET borrowed_from = 'KE-MID' WHERE code = 'KE-CHILD'")
                _flatten_tube_borrowing(connection)
                self.assertEqual(connection.execute("SELECT borrowed_from FROM tube_items WHERE code = 'KE-CHILD'").fetchone()[0], "KE-ROOT")


if __name__ == "__main__":
    unittest.main()
