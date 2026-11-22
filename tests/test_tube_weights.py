from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import connect
from app.modules.tubes.domain import row_from_2026
from app.modules.tubes.repository import TubeRepository, TubeUnitOfWork
from app.modules.tubes.service import TubeService
from app.modules.tubes.weights import calculate_single_weight, normalize_weight_values


def inputs(**changes):
    return {"code": "KE8001", "tube_type": "普通管", "spec_text": "ø41×34.5×41", "weight_mode": "auto",
            "outer_diameter_mm": "41", "inner_diameter_mm": "34.5", "weight_length_mm": "41.5",
            "tolerance_mm": "0.25", "consumption_mm": "3", **changes}


class TubeWeightCalculationTests(unittest.TestCase):
    def test_workbook_example_uses_mm_and_one_tube(self):
        self.assertAlmostEqual(calculate_single_weight(inputs()), 0.1353899503125, places=14)
        self.assertEqual(calculate_single_weight(inputs(purchase_base=2, quantity=3)), calculate_single_weight(inputs()))
        self.assertAlmostEqual(calculate_single_weight(inputs(weight_length_mm="83", tolerance_mm=0, consumption_mm=0)),
                               2 * calculate_single_weight(inputs(tolerance_mm=0, consumption_mm=0)), places=14)

    def test_missing_is_not_zero_and_zero_allowances_are_valid(self):
        for field in ("outer_diameter_mm", "inner_diameter_mm", "weight_length_mm", "tolerance_mm", "consumption_mm"):
            with self.subTest(field=field):
                self.assertIsNone(calculate_single_weight(inputs(**{field: ""})))
        self.assertGreater(calculate_single_weight(inputs(inner_diameter_mm=0, tolerance_mm=0, consumption_mm=0)), 0)

    def test_invalid_dimensions_never_produce_weight(self):
        for change in ({"outer_diameter_mm": "nan"}, {"consumption_mm": "Infinity"}, {"weight_length_mm": "0"},
                       {"outer_diameter_mm": "0"}, {"tolerance_mm": "-0.1"}, {"inner_diameter_mm": "41"},
                       {"inner_diameter_mm": "42"}, {"inner_diameter_mm": "-1"}, {"weight_length_mm": True},
                       {"outer_diameter_mm": "1e309"}, {"outer_diameter_mm": "1e308"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                calculate_single_weight(inputs(**change))

    def test_automatic_result_ignores_client_weight_and_manual_preserves_precision(self):
        result = normalize_weight_values(inputs(weight_kg="999"))
        self.assertAlmostEqual(result["weight_kg"], 0.1353899503125, places=14)
        old = {"weight_mode": "manual", "weight_length_mm": None, "weight_kg": 0.123456789012345}
        self.assertEqual(normalize_weight_values({}, old), old)
        self.assertIsNone(normalize_weight_values({"weight_kg": ""}, old)["weight_kg"])
        self.assertEqual(normalize_weight_values({"weight_kg": "0"}, old)["weight_kg"], 0)
        with self.assertRaisesRegex(ValueError, "计算方式"):
            normalize_weight_values(inputs(weight_mode="guess"))
        with self.assertRaises(ValueError):
            normalize_weight_values({"weight_kg": "nan"})

    def test_borrowed_automatic_row_does_not_calculate_independent_weight(self):
        self.assertIsNone(normalize_weight_values(inputs(borrowed_from="KE-ROOT", weight_kg="999"))["weight_kg"])

    def test_special_KG6028_requires_manual_without_changing_other_KG_codes(self):
        for code in ("KG6028", "kg6028", " KG6028 "):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "KG6028.*手工"):
                normalize_weight_values(inputs(code=code))
        old = {"code": "KG6028", "weight_mode": "manual", "weight_length_mm": None,
               "weight_kg": 0.139480128}
        self.assertEqual(normalize_weight_values({}, old)["weight_kg"], 0.139480128)
        with self.assertRaisesRegex(ValueError, "KG6028.*手工"):
            normalize_weight_values({"weight_mode": "auto"}, old)
        self.assertEqual(normalize_weight_values(inputs(code="KG8097"))["weight_mode"], "auto")


class TubeWeightPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "tubes.sqlite3"
        with connect(self.path):
            pass
        self.service = TubeService(lambda: TubeUnitOfWork(self.path))

    def test_save_recalculates_then_switching_manual_preserves_selected_value(self):
        item_id = self.service.save(inputs(weight_kg="999"), actor="test")
        initial = self.service.get_item(item_id)
        self.assertEqual(initial["weight_mode"], "auto")
        self.assertAlmostEqual(initial["weight_kg"], 0.1353899503125)
        self.service.save({**initial, "weight_length_mm": "50", "weight_kg": "999"}, actor="test")
        updated = self.service.get_item(item_id)
        self.assertAlmostEqual(updated["weight_kg"], calculate_single_weight(inputs(weight_length_mm=50)))
        self.service.save({**updated, "weight_mode": "manual", "weight_kg": "0.123456789012345"}, actor="test")
        self.assertEqual(self.service.get_item(item_id)["weight_kg"], 0.123456789012345)

    def test_missing_input_clears_automatic_weight_instead_of_reusing_old_value(self):
        item_id = self.service.save(inputs(), actor="test")
        current = self.service.get_item(item_id)
        self.service.save({**current, "weight_length_mm": ""}, actor="test")
        self.assertIsNone(self.service.get_item(item_id)["weight_kg"])
        self.assertEqual(self.service.costing_candidates("KE8001"), [])

    def test_omitted_parameters_remain_consistent_with_saved_automatic_weight(self):
        item_id = self.service.save(inputs(), actor="test")
        self.service.save({"id": item_id, "code": "KE8001", "tube_type": "普通管", "spec_text": "ø41×34.5×41", "note": "核对"}, actor="test")
        current = self.service.get_item(item_id)
        self.assertEqual(current["outer_diameter_mm"], 41)
        self.assertAlmostEqual(current["weight_kg"], calculate_single_weight(current))

    def test_borrowed_cost_weight_tracks_source_and_never_falls_back_to_stale_copy(self):
        source_id = self.service.save(inputs(), actor="test")
        self.service.save(inputs(code="KE-CHILD", borrowed_from="KE8001", weight_mode="manual", weight_kg=99), actor="test")
        self.assertAlmostEqual(float(self.service.costing_candidates("KE-CHILD")[0]["gross_weight"]), 0.1353899503125)
        source = self.service.get_item(source_id)
        self.service.save({**source, "weight_length_mm": 50}, actor="test")
        self.assertAlmostEqual(float(self.service.costing_candidates("KE-CHILD")[0]["gross_weight"]), calculate_single_weight(inputs(weight_length_mm=50)))
        self.service.save({**self.service.get_item(source_id), "active": "0"}, actor="test")
        self.assertEqual(self.service.costing_candidates("KE-CHILD"), [])

    def test_inactive_source_hides_borrowed_snapshot_from_list_and_weight_filters(self):
        source_id = self.service.save(inputs(), actor="test")
        child_id = self.service.save(inputs(code="KE-CHILD", borrowed_from="KE8001", weight_mode="manual", weight_kg=99), actor="test")
        self.service.save(inputs(code="KE-PLAIN", weight_mode="manual", weight_kg=99), actor="test")
        self.service.save({**self.service.get_item(source_id), "weight_length_mm": 50, "active": "0"}, actor="test")

        listing = self.service.list_items(filters={}, limit=100, offset=0)
        rows = {row["code"]: row for row in listing["records"]}
        self.assertEqual(listing["total"], 2)
        self.assertIsNone(rows["KE-CHILD"]["weight_kg"])
        self.assertEqual(self.service.get_item(child_id)["weight_kg"], 99)
        self.assertEqual(self.service.costing_candidates("KE-CHILD"), [])
        for filters in ({"weight_eq": 99}, {"weight_min": 1}, {"weight_max": 100}):
            with self.subTest(filters=filters):
                filtered = self.service.list_items(filters=filters, limit=100, offset=0)
                self.assertEqual(filtered["total"], 1)
                self.assertEqual([row["code"] for row in filtered["records"]], ["KE-PLAIN"])

    def test_excel_length_uses_numeric_O_and_cannot_overwrite_auto_record(self):
        values = [None] * 19
        values[3:7] = ["KE8001", "ø41×34.5×41", "ø41×34.5", "34.5-0.2"]
        values[8] = "41±0.1"
        values[9] = "焊接管"
        values[12:19] = [41, 34.5, .0415, .00025, .003, 1, .1353899503125]
        row = row_from_2026(values, 10)
        self.assertEqual(row.weight_length_mm, 41.5)
        self.assertEqual(row.blank_length_text, "41±0.1")
        with connect(self.path) as connection:
            repository = TubeRepository(connection)
            repository.import_rows([row], actor="test")
            connection.commit()
            record = dict(connection.execute("SELECT * FROM tube_items WHERE code='KE8001'").fetchone())
        self.assertEqual(record["weight_mode"], "manual")
        self.service.save({**record, "weight_mode": "auto"}, actor="test")
        with self.assertRaisesRegex(ValueError, "自动计重"):
            with TubeUnitOfWork(self.path) as unit:
                unit.repository.import_rows([row], actor="test")
                unit.commit()
        self.assertEqual(self.service.get_item(record["id"])["weight_mode"], "auto")
