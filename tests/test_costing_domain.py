from __future__ import annotations

import unittest
from decimal import Decimal

from app.modules.costing.domain import CostingValidationError, calculate_cost


class CostingDomainTest(unittest.TestCase):
    @staticmethod
    def sheet(items, **overrides):
        return {"code": " k6001b ", "kind": "arm", "items": items, **overrides}

    def test_real_packaging_weight_piece_and_credit_rules_are_separate(self):
        result = calculate_cost(self.sheet([
            {"name": "主板", "mode": "weight", "unit_price": "4.5", "weight": "1.23"},
            {"name": "焊接管", "mode": "weight", "unit_price": "6.5", "weight": "0.095", "processing_fee": "0.3"},
            {"name": "螺栓", "mode": "unit", "unit_price": "0.25", "quantity": "3"},
            {"name": "纸箱", "mode": "package", "unit_price": "6.1", "quantity": "20",
             "length": "56", "width": "42", "height": "12", "strap_rate": "0.056"},
            {"name": "运费", "mode": "freight", "unit_price": "0.6", "weight": "1.5"},
            {"name": "返还", "mode": "adjustment", "amount": "-0.2"},
        ], approved_cost="8", scrap_credit="0.5", theoretical_scrap="0.8"))
        self.assertEqual(result["code"], "K6001B")
        self.assertEqual([item["line_total"] for item in result["items"]],
                         ["5.535", "0.9175", "0.75", "0.324152", "0.9", "-0.2"])
        self.assertEqual(result["calculated_cost"], "8.226652")
        self.assertEqual(result["effective_cost"], "8")
        self.assertEqual(result["net_cost"], "7.5")
        self.assertEqual(result["theoretical_scrap"], "0.8")
        self.assertEqual(result["status"], "ready")

    def test_missing_is_not_zero_and_approved_zero_is_intentional(self):
        result = calculate_cost(self.sheet([
            {"name": "已知", "amount": "2.5"}, {"name": "未知", "amount": None},
        ], approved_cost="0"))
        self.assertEqual(result["known_subtotal"], "2.5")
        self.assertIsNone(result["calculated_cost"])
        self.assertEqual(result["effective_cost"], "0")
        self.assertEqual(result["net_cost"], "0")
        self.assertEqual(result["status"], "draft")
        result = calculate_cost(self.sheet([{"name": "免收费用", "amount": "0"}]))
        self.assertEqual(result["calculated_cost"], "0")
        self.assertEqual(result["status"], "ready")

    def test_weight_quantity_and_processing_fee_are_per_piece(self):
        result = calculate_cost(self.sheet([
            {"name": "支撑管", "mode": "weight", "unit_price": "5", "weight": "0.1",
             "processing_fee": "0.2", "quantity": "3"},
        ]))
        self.assertEqual(result["calculated_cost"], "2.1")

    def test_decimal_arithmetic_does_not_turn_tenths_into_binary_rounding(self):
        result = calculate_cost(self.sheet([
            {"name": "材料", "mode": "unit", "unit_price": "0.1", "quantity": "3"},
            {"name": "工费", "amount": "0.7"},
        ]))
        self.assertEqual(result["calculated_cost"], "1")
        self.assertIsInstance(result["items"][0]["unit_price"], str)

    def test_box_zero_divisor_is_rejected_and_missing_divisor_is_draft(self):
        item = {"name": "包装", "mode": "package", "unit_price": "4", "strap_rate": "0"}
        result = calculate_cost(self.sheet([item]))
        self.assertIsNone(result["calculated_cost"])
        self.assertIn("每箱装几只", " ".join(result["issues"]))
        with self.assertRaisesRegex(CostingValidationError, "必须大于"):
            calculate_cost(self.sheet([{**item, "quantity": "0"}]))
        result = calculate_cost(self.sheet([{**item, "quantity": "2"}]))
        self.assertEqual(result["calculated_cost"], "2")

    def test_invalid_numbers_and_overflow_are_rejected(self):
        for amount in (True, "NaN", "Infinity", "-Infinity", "bad", "1e99", "1e1000000", "1e-999", "-0.01"):
            with self.subTest(amount=amount), self.assertRaises(CostingValidationError):
                calculate_cost(self.sheet([{"name": "费用", "amount": amount}]))
        with self.assertRaisesRegex(CostingValidationError, "计算金额"):
            calculate_cost(self.sheet([{"name": "费用", "mode": "unit", "unit_price": "1e12", "quantity": "2"}]))

    def test_review_notes_and_negative_scrap_require_attention(self):
        result = calculate_cost(self.sheet([
            {"name": "待核对工费", "amount": "2", "review_note": "原表复杂公式，保留原值待核对"},
        ], theoretical_scrap="-0.4"))
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["theoretical_scrap"], "-0.4")
        self.assertEqual(result["calculated_cost"], "2")
        self.assertEqual(len(result["issues"]), 2)

    def test_missing_name_and_empty_sheet_remain_savable_drafts(self):
        for items in ([], [{"amount": "1"}]):
            with self.subTest(items=items):
                result = calculate_cost(self.sheet(items))
                self.assertEqual(result["status"], "draft")
                self.assertTrue(result["issues"])

    def test_sources_are_deep_copied_and_rejected_if_not_json(self):
        origin = {"sheet": "摆臂成本核算", "raw": {"H8": "=I8*K8"}}
        result = calculate_cost(self.sheet([{"name": "主板", "amount": "5.535", "source_info": origin}]))
        origin["raw"]["H8"] = "modified"
        self.assertEqual(result["items"][0]["source_info"]["raw"]["H8"], "=I8*K8")
        with self.assertRaises(CostingValidationError):
            calculate_cost(self.sheet([], source_info={"invalid": Decimal("1")}))


if __name__ == "__main__":
    unittest.main()
