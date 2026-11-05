from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import unittest

from app.modules.costing.domain import CostingValidationError, calculate_cost, with_pricing


class CostingPricingTest(unittest.TestCase):
    @staticmethod
    def sheet(amount="100", **overrides):
        return {"code": "KTESTLA", "kind": "arm", "items": [{"name": "材料", "amount": amount}], **overrides}

    def test_minimum_reserves_management_and_profit_then_applies_tax_multiplier(self):
        result = calculate_cost(self.sheet())
        self.assertEqual(result["net_cost"], "100")
        self.assertEqual(result["minimum_tax_price"], "137.5")
        self.assertEqual(result["suggested_tax_price"], "137.5")
        self.assertIsNone(result["suggested_tax_price_override"])
        self.assertEqual(Decimal(result["minimum_tax_price"]) / Decimal("1.1") * Decimal("0.8"), Decimal("100"))

    def test_minimum_uses_approved_cost_after_actual_scrap_credit(self):
        result = calculate_cost(self.sheet(approved_cost="90", scrap_credit="10", theoretical_scrap="25"))
        self.assertEqual(result["calculated_cost"], "100")
        self.assertEqual(result["net_cost"], "80")
        self.assertEqual(result["minimum_tax_price"], "110")

    def test_minimum_rounds_only_after_tax_multiplier_without_rounding_cost(self):
        result = calculate_cost(self.sheet("1.001"))
        self.assertEqual(result["net_cost"], "1.001")
        # Rounding 1.001 / 0.8 first would yield 1.39 after tax; the correct final-only rounding is 1.38.
        self.assertEqual(result["minimum_tax_price"], "1.38")
        precise = with_pricing({"net_cost": "0.33333333333333333333333333333333333333333333333333"})
        self.assertEqual(precise["minimum_tax_price"], "0.46")

    def test_manual_price_survives_cost_changes_and_blank_returns_to_automatic(self):
        original = calculate_cost(self.sheet(suggested_tax_price_override="130.1234"))
        changed = calculate_cost({**original, "items": [{"name": "材料", "amount": "120"}]})
        self.assertEqual(changed["minimum_tax_price"], "165")
        self.assertEqual(changed["suggested_tax_price"], "130.1234")
        self.assertEqual(changed["suggested_tax_price_override"], "130.1234")
        automatic = calculate_cost({**changed, "suggested_tax_price_override": ""})
        self.assertEqual(automatic["suggested_tax_price"], "165")
        self.assertIsNone(automatic["suggested_tax_price_override"])

    def test_zero_is_an_intentional_manual_price(self):
        result = calculate_cost(self.sheet(suggested_tax_price_override="0"))
        self.assertEqual(result["minimum_tax_price"], "137.5")
        self.assertEqual(result["suggested_tax_price"], "0")
        self.assertEqual(result["suggested_tax_price_override"], "0")
        self.assertEqual(calculate_cost(self.sheet("0"))["minimum_tax_price"], "0")

    def test_unknown_and_negative_net_cost_have_no_automatic_sale_price(self):
        for data in (self.sheet(None), self.sheet("1", scrap_credit="2")):
            with self.subTest(data=data):
                result = calculate_cost(data)
                self.assertIsNone(result["minimum_tax_price"])
                self.assertIsNone(result["suggested_tax_price"])
                manual = calculate_cost({**data, "suggested_tax_price_override": "2.5"})
                self.assertIsNone(manual["minimum_tax_price"])
                self.assertEqual(manual["suggested_tax_price"], "2.5")

    def test_legacy_read_enrichment_preserves_source_versions_and_input(self):
        legacy = {
            "id": "stable-id", "code": "KTESTLA", "net_cost": "19.783652", "version": 7,
            "updated_at": "2026-11-05T00:00:00Z", "source_info": {"sheet": "摆臂成本核算"},
            "items": [{"id": "stable-line-id", "amount": "19.783652"}],
        }
        before = deepcopy(legacy)
        result = with_pricing(legacy)
        self.assertEqual(legacy, before)
        self.assertEqual({key: result[key] for key in legacy}, legacy)
        self.assertEqual(result["minimum_tax_price"], "27.21")
        self.assertEqual(result["suggested_tax_price"], "27.21")
        self.assertIsNone(result["suggested_tax_price_override"])
        self.assertEqual(with_pricing(result), result)

    def test_derived_prices_cannot_be_overridden_as_inputs(self):
        result = calculate_cost(self.sheet(minimum_tax_price="1", suggested_tax_price="2"))
        self.assertEqual(result["minimum_tax_price"], "137.5")
        self.assertEqual(result["suggested_tax_price"], "137.5")

    def test_manual_price_validation_uses_existing_decimal_boundaries(self):
        for value in (True, "-0.01", "NaN", "Infinity", "bad", "1e1000000", "0.1234567890123456789"):
            with self.subTest(value=value), self.assertRaises(CostingValidationError) as error:
                calculate_cost(self.sheet(suggested_tax_price_override=value))
            self.assertEqual(error.exception.field, "suggested_tax_price_override")


if __name__ == "__main__":
    unittest.main()
