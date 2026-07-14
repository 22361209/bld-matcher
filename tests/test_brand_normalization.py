from __future__ import annotations

import unittest

from app.modules.products.brand_normalization import canonicalize_brands


class BrandNormalizationTest(unittest.TestCase):
    def test_canonicalizes_confirmed_legacy_values(self) -> None:
        cases = {
            "MERCEDES-\nBENZ": "MERCEDES-BENZ",
            "MAZADA": "MAZDA",
            "FORD/MAZDA": "FORD\nMAZDA",
            "FORD/VOLVO": "FORD\nVOLVO",
            "DODGE CHRYSLER": "DODGE\nCHRYSLER",
            "DODGE RAM": "DODGE",
            "JEEP CHRYSLER": "JEEP\nCHRYSLER",
            "JEEP DODGE": "JEEP\nDODGE",
            "MG ROEWE": "MG\nROEWE",
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(canonicalize_brands(source), expected)

    def test_uppercases_all_brands_and_cleans_whitespace(self) -> None:
        self.assertEqual(
            canonicalize_brands("  volkswagen \r\n\tGreat   Wall\t\r volvo  "),
            "VOLKSWAGEN\nGREAT WALL\nVOLVO",
        )

    def test_does_not_split_unconfirmed_multi_word_brand(self) -> None:
        self.assertEqual(canonicalize_brands("GREAT WALL\nLAND ROVER"), "GREAT WALL\nLAND ROVER")

    def test_splits_slash_delimited_brands_with_optional_whitespace(self) -> None:
        self.assertEqual(
            canonicalize_brands("ford / mazda / volvo"),
            "FORD\nMAZDA\nVOLVO",
        )

    def test_maps_independent_ram_token_to_dodge(self) -> None:
        self.assertEqual(canonicalize_brands("RAM"), "DODGE")
        self.assertEqual(canonicalize_brands("FORD\nRAM\nJEEP"), "FORD\nDODGE\nJEEP")
        self.assertEqual(canonicalize_brands("RAM/FORD"), "DODGE\nFORD")
        self.assertEqual(canonicalize_brands("RAM TRUCKS"), "DODGE")
        self.assertEqual(canonicalize_brands("RAMBO"), "RAMBO")

    def test_deduplicates_after_aliasing_and_preserves_first_seen_order(self) -> None:
        self.assertEqual(
            canonicalize_brands("RAM\ndodge\nFORD/DODGE\nford\nMAZADA\nmazda"),
            "DODGE\nFORD\nMAZDA",
        )

    def test_joins_legacy_mercedes_name_inside_a_brand_list(self) -> None:
        self.assertEqual(
            canonicalize_brands("AUDI\n mercedes- \r\n benz \nBMW"),
            "AUDI\nMERCEDES-BENZ\nBMW",
        )

    def test_handles_empty_values(self) -> None:
        self.assertEqual(canonicalize_brands(None), "")
        self.assertEqual(canonicalize_brands(" \r\n\t"), "")

    def test_is_idempotent(self) -> None:
        sources = (
            "MERCEDES-\nBENZ",
            "DODGE RAM\nFORD/MAZDA\nGREAT WALL",
            "volkswagen\nVolvo\nRAM\nDODGE",
            "",
        )

        for source in sources:
            with self.subTest(source=source):
                once = canonicalize_brands(source)
                self.assertEqual(canonicalize_brands(once), once)


if __name__ == "__main__":
    unittest.main()
