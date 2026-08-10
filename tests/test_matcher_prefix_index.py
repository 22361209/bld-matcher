from __future__ import annotations

import copy
import re
import unittest

from app.matcher import CatalogMatch, ProductCatalog, compact_text


class ScanningReferenceProductCatalog(ProductCatalog):
    """Pre-bisect prefix behavior retained only as a differential test oracle."""

    def _match_unique_oe_prefix(self, key: str, inquiry_oe: object) -> CatalogMatch | None:
        if len(key) < 5:
            return None

        rows: list[dict] = []
        fields: set[str] = set()
        for code_key, code_rows in self.by_oe.items():
            if not code_key.startswith(key) or code_key == key:
                continue
            rows.extend(code_rows)
            fields.update(self.by_oe_fields.get(code_key, set()))

        unique_rows = self._unique_rows(rows)
        if len(unique_rows) != 1:
            return None

        row = unique_rows[0]
        reason = "品牌号码组合前缀命中" if fields == {"OE NO.2"} else "OE 组合前缀命中"
        return CatalogMatch(
            compact_text(row.get("BLD NO.")),
            96,
            reason,
            row,
            matched_codes=((compact_text(inquiry_oe),) if compact_text(inquiry_oe) else ()),
        )

    def _match_oe_suffix_variant(self, key: str, inquiry_oe: object) -> CatalogMatch | None:
        suffix_match = re.fullmatch(r"(.+\d)[A-Z]+", key)
        if not suffix_match:
            return None

        base_key = suffix_match.group(1)
        if len(base_key) < 5:
            return None

        rows: list[dict] = []
        fields: set[str] = set()
        for code_key, code_rows in self.by_oe.items():
            if code_key.startswith(base_key):
                rows.extend(code_rows)
                fields.update(self.by_oe_fields.get(code_key, set()))

        unique_rows = self._unique_rows(rows)
        if len(unique_rows) != 1:
            return None

        row = unique_rows[0]
        reason = "品牌号码尾字母容错命中" if fields == {"OE NO.2"} else "OE 尾字母容错命中"
        return CatalogMatch(
            compact_text(row.get("BLD NO.")),
            89,
            reason,
            row,
            matched_codes=((compact_text(inquiry_oe),) if compact_text(inquiry_oe) else ()),
        )


def catalog_rows() -> list[dict[str, str]]:
    return [
        {
            "BLD NO.": "K54500L",
            "SERIES": "HYUNDAI",
            "ITEM": "Front Lower Control Arm",
            "OE NO.1": "54500-2D000",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K8282RA",
            "SERIES": "FORD",
            "ITEM": "Front Right Lower Control Arm",
            "OE NO.1": "F1F1-3A423-AAA\nF1F1-3A423-AAB",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K-AMB-A",
            "SERIES": "TEST",
            "ITEM": "Ambiguous A",
            "OE NO.1": "AMB-54321-A",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K-AMB-B",
            "SERIES": "TEST",
            "ITEM": "Ambiguous B",
            "OE NO.1": "AMB-54321-B",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K8041LB",
            "SERIES": "VW",
            "ITEM": "Front Left Lower Control Arm",
            "OE NO.1": "561407151A\n561407151C",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K-OZERO",
            "SERIES": "TEST",
            "ITEM": "O zero tolerance",
            "OE NO.1": "AB0-90001",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K-PSA-352123",
            "SERIES": "PEUGEOT\nCITROEN",
            "ITEM": "PSA Arm",
            "OE NO.1": "3521.23",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K-GM-352123",
            "SERIES": "GM\nOPEL",
            "ITEM": "GM Arm",
            "OE NO.1": "352123",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K8057LB",
            "SERIES": "HYUNDAI",
            "ITEM": "Multi-code Arm",
            "OE NO.1": "54500-1E000\n54500-1E100",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K-MULTI-B",
            "SERIES": "TEST",
            "ITEM": "Second multi-code product",
            "OE NO.1": "MULTI-20002",
            "OE NO.2": "",
        },
        {
            "BLD NO.": "K-BRAND",
            "SERIES": "TEST",
            "ITEM": "Brand code product",
            "OE NO.1": "",
            "OE NO.2": "BRAND: 77777A\nBRAND: 77777B",
        },
    ]


class MatcherPrefixIndexEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        rows = catalog_rows()
        manual_map = {"MANUAL001": "K54500L"}
        self.optimized = ProductCatalog(copy.deepcopy(rows), manual_map=manual_map)
        self.reference = ScanningReferenceProductCatalog(copy.deepcopy(rows), manual_map=manual_map)

    def assert_reference_match(self, inquiry_name: object, inquiry_oe: object) -> CatalogMatch | None:
        optimized = self.optimized.match(inquiry_name, inquiry_oe)
        reference = self.reference.match(inquiry_name, inquiry_oe)
        self.assertEqual(optimized, reference)
        return optimized

    def test_named_contract_cases_match_scanning_reference_and_expected_result(self) -> None:
        cases = (
            ("bld_exact", "", "K54500L", "K54500L", "BLD NO. 精准命中"),
            ("bld_from_name", "K54500L", "", "K54500L", "BLD NO. 精准命中"),
            ("oe_exact", "", "54500-2D000", "K54500L", "OE 精准命中"),
            ("unique_prefix", "", "F1F1 3A423", "K8282RA", "OE 组合前缀命中"),
            ("ambiguous_prefix", "", "AMB-54321", None, None),
            ("suffix_variant", "", "561407151D", "K8041LB", "OE 尾字母容错命中"),
            ("zero_o", "", "ABO-90001", "K-OZERO", "OE 字符容错命中"),
            ("psa_dot", "", "3521.23", "K-PSA-352123", "PSA 号码点号容错命中"),
            (
                "psa_ambiguous",
                "",
                "352123",
                "K-PSA-352123 / K-GM-352123",
                "3520/3521 号码同时命中 PSA 与其他品牌，请人工确认",
            ),
            ("brand_prefix", "", "77777", "K-BRAND", "品牌号码组合前缀命中"),
            ("brand_suffix", "", "77777Z", "K-BRAND", "品牌号码尾字母容错命中"),
            ("manual_map", "", "MANUAL-001", "K54500L", "人工确认映射"),
        )

        for label, inquiry_name, inquiry_oe, expected_bld, expected_reason in cases:
            with self.subTest(label=label):
                match = self.assert_reference_match(inquiry_name, inquiry_oe)
                if expected_bld is None:
                    self.assertIsNone(match)
                else:
                    self.assertIsNotNone(match)
                    assert match is not None
                    self.assertEqual(match.bld_no, expected_bld)
                    self.assertEqual(match.reason, expected_reason)

    def test_multi_number_matches_and_return_order_match_scanning_reference(self) -> None:
        partial = self.assert_reference_match("", "545001E000/545001E999\n545001E000")
        self.assertIsNotNone(partial)
        assert partial is not None
        self.assertEqual(partial.bld_no, "K8057LB")
        self.assertEqual(partial.matched_codes, ("545001E000", "545001E000"))
        self.assertEqual(partial.unmatched_codes, ("545001E999",))

        ordered = self.assert_reference_match("", "MULTI-20002;54500-1E000")
        self.assertIsNotNone(ordered)
        assert ordered is not None
        self.assertEqual(ordered.bld_no, "K-MULTI-B / K8057LB")
        self.assertEqual(ordered.matched_codes, ("MULTI-20002", "54500-1E000"))

    def test_generated_bld_oe_prefix_suffix_and_tolerant_corpus_matches_reference(self) -> None:
        queries = set(self.optimized.by_bld)
        for key in self.optimized.by_oe:
            queries.add(key)
            queries.update(key[:length] for length in range(5, len(key)))
            if key[-1:].isalpha():
                queries.add(f"{key[:-1]}Z")
            if "O" in key:
                queries.add(key.replace("O", "0"))
            if "0" in key:
                queries.add(key.replace("0", "O"))
        queries.update(
            {
                "NO-MATCH-00001",
                "3521.23",
                "545001E000/545001E999",
                "MULTI-20002;54500-1E000",
            }
        )

        for query in sorted(queries):
            with self.subTest(query=query):
                self.assert_reference_match("", query)

    def test_brand_prefix_equivalence_rules_generate_cross_prefix_aliases(self) -> None:
        rows = [
            {
                "BLD NO.": "K-MOOG",
                "SERIES": "TEST",
                "ITEM": "Moog arm",
                "OE NO.1": "",
                "OE NO.2": "Moog: RK623344",
            },
            {
                "BLD NO.": "K-MEVO",
                "SERIES": "TEST",
                "ITEM": "Mevotech arm",
                "OE NO.1": "",
                "OE NO.2": "Mevotech: CMS801114",
            },
        ]
        catalog = ProductCatalog(rows)
        cases = (
            ("moog_k_variant", "", "K623344", "K-MOOG", "品牌号码精准命中"),
            ("moog_ck_variant", "", "CK623344", "K-MOOG", "品牌号码精准命中"),
            ("moog_rk_variant", "", "RK623344", "K-MOOG", "品牌号码精准命中"),
            ("mevotech_ms_variant", "", "MS801114", "K-MEVO", "品牌号码精准命中"),
            ("mevotech_gs_variant", "", "GS801114", "K-MEVO", "品牌号码精准命中"),
            ("mevotech_cms_variant", "", "CMS801114", "K-MEVO", "品牌号码精准命中"),
        )
        for label, inquiry_name, inquiry_oe, expected_bld, expected_reason in cases:
            with self.subTest(label=label):
                match = catalog.match(inquiry_name, inquiry_oe)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.bld_no, expected_bld)
                self.assertEqual(match.reason, expected_reason)

    def test_sorted_index_contains_every_oe_key_once(self) -> None:
        self.assertEqual(self.optimized._sorted_oe_keys, tuple(sorted(self.optimized.by_oe)))


if __name__ == "__main__":
    unittest.main()
