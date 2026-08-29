from __future__ import annotations

import unittest

from app.modules.inquiry.excel.analysis import summary_row
from app.matcher import ProductCatalog
from app.product_variants import build_variant_groups, default_variant, variant_base


def _row(bld_no: str, oe: str = "", status: str = "", price: float | None = None) -> dict:
    return {
        "BLD NO.": bld_no,
        "OE NO.1": oe,
        "OE NO.2": "",
        "product_status": status,
        "price_cny": price,
    }


class VariantBaseTest(unittest.TestCase):
    def test_trailing_status_letter_splits_base(self) -> None:
        self.assertEqual(variant_base("K8053LA"), ("K8053L", "A"))
        self.assertEqual(variant_base("K8053LB"), ("K8053L", "B"))
        self.assertEqual(variant_base("K500044B"), ("K500044", "B"))

    def test_side_suffix_and_dash_variants_are_not_status_variants(self) -> None:
        self.assertIsNone(variant_base("K8053L"))
        self.assertIsNone(variant_base("K8053R"))
        self.assertIsNone(variant_base("K8053LA-1"))
        self.assertIsNone(variant_base("K8053"))
        self.assertIsNone(variant_base(""))


class VariantGroupTest(unittest.TestCase):
    def test_groups_require_two_distinct_letters(self) -> None:
        rows = [
            _row("K8053LA"),
            _row("K8053LB"),
            _row("K9999A"),
        ]
        groups = build_variant_groups(rows)
        self.assertEqual(sorted(groups), ["K8053L"])
        self.assertEqual([row["BLD NO."] for row in groups["K8053L"]], ["K8053LA", "K8053LB"])

    def test_default_prefers_letter_a_then_ball_joint_status(self) -> None:
        members = build_variant_groups([_row("K8053LB"), _row("K8053LA")])["K8053L"]
        self.assertEqual(default_variant(members)["BLD NO."], "K8053LA")

        no_a = [
            _row("K8053LB", status="2个衬套"),
            _row("K8053LC", status="2个衬套1个球头"),
        ]
        self.assertEqual(default_variant(no_a)["BLD NO."], "K8053LC")

        plain = [_row("K8053LB"), _row("K8053LC")]
        self.assertEqual(default_variant(plain)["BLD NO."], "K8053LB")


class CatalogVariantMatchTest(unittest.TestCase):
    def test_shared_oe_defaults_to_ball_joint_variant_regardless_of_row_order(self) -> None:
        for rows in (
            [_row("K8053LA", "SHARED-OE", "带球头"), _row("K8053LB", "SHARED-OE")],
            [_row("K8053LB", "SHARED-OE"), _row("K8053LA", "SHARED-OE", "带球头")],
        ):
            with self.subTest(first=rows[0]["BLD NO."]):
                catalog = ProductCatalog(rows)
                match = catalog.match("", "SHARED-OE")
                self.assertIsNotNone(match)
                self.assertEqual(match.bld_no, "K8053LA")

    def test_variant_options_for_returns_same_model_members(self) -> None:
        catalog = ProductCatalog([_row("K8053LA"), _row("K8053LB"), _row("K9999A")])
        options = catalog.variant_options_for("K8053LB")
        self.assertEqual([row["BLD NO."] for row in options], ["K8053LA", "K8053LB"])
        self.assertEqual(catalog.variant_options_for("K9999A"), [])
        self.assertEqual(catalog.variant_options_for("K8053L"), [])

    def test_same_model_split_conflict_resolves_to_default_variant(self) -> None:
        catalog = ProductCatalog([
            _row("K8053LA", "OE-LA", "带球头"),
            _row("K8053LB", "OE-LB"),
        ])
        match = catalog.match("", "OE-LA\nOE-LB")
        self.assertIsNotNone(match)
        self.assertEqual(match.bld_no, "K8053LA")
        self.assertNotIn(" / ", match.bld_no)
        self.assertEqual(match.matched_codes, ("OE-LA", "OE-LB"))

    def test_cross_model_split_conflict_keeps_marker_and_carries_candidates(self) -> None:
        catalog = ProductCatalog([
            _row("K8053LA", "OE-LA", "带球头", 80),
            _row("K9999A", "OE-9999", "", 40),
        ])
        match = catalog.match("", "OE-LA\nOE-9999")
        self.assertIsNotNone(match)
        self.assertEqual(match.bld_no, "K8053LA / K9999A")
        self.assertEqual(
            [row["BLD NO."] for row in match.candidate_rows],
            ["K8053LA", "K9999A"],
        )


class SummaryRowOptionsTest(unittest.TestCase):
    def test_conflict_row_exposes_candidates_without_price_or_status(self) -> None:
        catalog = ProductCatalog([
            _row("K8053LA", "OE-LA", "带球头", 80),
            _row("K9999A", "OE-9999", "2个衬套", 40),
        ])
        match = catalog.match("", "OE-LA\nOE-9999")
        row = summary_row(2, "OE-LA\nOE-9999", "Arm", match)
        self.assertFalse(row["adjustment_allowed"])
        self.assertIsNone(row["price_cny"])
        self.assertEqual(
            [option["bld_no"] for option in row["conflict_candidates"]],
            ["K8053LA", "K9999A"],
        )
        self.assertEqual(row["conflict_candidates"][0]["price_cny"], 80)
        self.assertEqual(row["variant_options"], [])

    def test_unique_row_exposes_variant_options(self) -> None:
        catalog = ProductCatalog([
            _row("K8053LA", "OE-LA", "带球头", 80),
            _row("K8053LB", "OE-LB", "2个衬套", 58),
        ])
        match = catalog.match("", "OE-LA")
        row = summary_row(
            2,
            "OE-LA",
            "Arm",
            match,
            variant_option_rows=catalog.variant_options_for(match.bld_no),
        )
        self.assertTrue(row["adjustment_allowed"])
        self.assertEqual(row["conflict_candidates"], [])
        self.assertEqual(
            [option["bld_no"] for option in row["variant_options"]],
            ["K8053LA", "K8053LB"],
        )
        self.assertEqual(row["variant_options"][1]["product_status"], "2个衬套")


if __name__ == "__main__":
    unittest.main()
