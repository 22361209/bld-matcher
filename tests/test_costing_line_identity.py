from __future__ import annotations

import unittest

from app.modules.costing.line_identity import inspect_line_identity, line_identity_changes


class CostingLineIdentityTests(unittest.TestCase):
    def test_carton_retains_written_specification_without_using_the_part_number(self):
        for name, spec in (("54*41*15.5", "54*41*15.5"), ("纸箱：54 X 41 × 15.50 cm", "54 X 41 × 15.50 cm")):
            with self.subTest(name=name):
                item = {"category": "外箱", "name": name, "part_code": "ZX-A", "mode": "fixed"}
                updates = line_identity_changes(item)
                self.assertEqual(updates, {"category": "外包装", "name": "纸箱", "product_code": spec})
                self.assertEqual(item["name"], name)
                self.assertEqual(line_identity_changes({**item, **updates}), {})
                self.assertEqual(inspect_line_identity(item)[1], "")

    def test_generic_carton_has_no_synthetic_specification(self):
        for category, name in (("外箱", ""), ("外包装", "外箱"), ("外箱", "外包装"), ("包装", "外箱"), ("球头1", "纸箱")):
            with self.subTest(category=category, name=name):
                item = {"category": category, "name": name, "part_code": "ZX-A"}
                updated = {**item, **line_identity_changes(item)}
                self.assertEqual((updated["category"], updated["name"]), ("外包装", "纸箱"))
                self.assertNotIn("product_code", updated)
        item = {"category": "外箱", "name": "外箱", "product_code": "USER-CODE"}
        self.assertNotIn("product_code", line_identity_changes(item))

    def test_carton_detection_uses_context_and_preserves_uncertain_names(self):
        for category, mode in (("外包装", "fixed"), ("包装", "fixed"), ("", "package")):
            with self.subTest(category=category, mode=mode):
                result = line_identity_changes({"category": category, "name": "56*48*18", "mode": mode})
                self.assertEqual(result["product_code"], "56*48*18")
        self.assertEqual(line_identity_changes({"category": "加固管", "name": "46.5*41.5*6"}), {})
        self.assertEqual(line_identity_changes({"category": "外包装", "name": "泡沫"}), {})
        for name in ("内外箱", "外箱与内盒合计", "54*41*15.5 / 56*41*20"):
            with self.subTest(name=name):
                item = {"category": "外箱", "name": name}
                self.assertEqual(line_identity_changes(item), {})
                self.assertTrue(inspect_line_identity(item)[1])

    def test_existing_independent_product_code_is_preserved(self):
        for item in (
            {"name": "C8057", "product_code": "CUSTOM"},
            {"name": "54*41*15.5", "category": "外箱", "product_code": "OTHER"},
        ):
            self.assertEqual(line_identity_changes(item), {})
            self.assertTrue(inspect_line_identity(item)[1])
        self.assertEqual(line_identity_changes({"name": "C8057", "part_code": "OLD"}), {"product_code": "C8057"})
        self.assertEqual(inspect_line_identity({"name": "C8057", "product_code": "C8057"}), ({}, ""))

    def test_embedded_and_short_part_numbers_preserve_description_and_format(self):
        cases = (
            ("B8012", "B8012"), ("封板QA8001", "QA8001"), ("C6043-45", "C6043-45"),
            ("护套 ht023", "ht023"), ("G8354L", "G8354L"), ("HCK8354-564 缓冲块", "HCK8354-564"),
            ("球座（28D-5）", "28D-5"), ("球座 D-047", "D-047"), ("GD-28-2", "GD-28-2"),
            ("球座30球（YM0058）", "YM0058"), ("六角螺栓 BOLT023 M8*1.25*25", "BOLT023"),
            ("B8042 422g", "B8042"), ("D8023 6.5+2.7", "D8023"), ("BLT-12125-36-C13", "BLT-12125-36-C13"),
        )
        for name, code in cases:
            with self.subTest(name=name):
                item = {"name": name, "category": "原类别"}
                self.assertEqual(line_identity_changes(item), {"product_code": code})
                self.assertEqual(line_identity_changes({**item, "product_code": code}), {})

    def test_do_not_assign_composite_codes_thread_sizes_standards_or_weights(self):
        for name in ("双层球座\nB-081\nC-039.2", "C8355 8045 滚齿", "B8012/C8012"):
            with self.subTest(name=name):
                self.assertEqual(line_identity_changes({"name": name}), {})
                self.assertTrue(inspect_line_identity({"name": name})[1])
        for name in ("3KG", "35*2", "16#", "滚花螺栓M12*1.25*47", "压小套2支", "DIN7991", "球墨铸铁450"):
            with self.subTest(name=name):
                self.assertEqual(line_identity_changes({"name": name}), {})
