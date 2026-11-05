from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from app.modules.costing.historical_materials import (
    extract_estimated_rows, material_code_evidence, material_match_key, plan_materials,
)


def reference(**fields):
    return {"code": "8057", "name": "主板", "net_weight": "0.95", "gross_weight": "2.19",
            "source_info": {"sheet": "片件重量", "row": 99, "raw": {"E": "0.95"}}, **fields}


def material(**fields):
    return {"model": "8057", "part": "主板", "code": "KA8057", "id": 5, "sync_id": "material-1", "active": 1,
            "net_weight": None, "spec_text": "2.5×357×1260", "thickness": 2.5, "width": 357, "length": 1260,
            "pieces": 4, **fields}


def record(code="K8057LB", **fields):
    source = {"sheet": "摆臂成本核算", "scrap_reference_rows": [reference()]}
    item = {"name": "主板", "mode": "weight", "part_code": "KA8057", "product_code": "",
            "source_info": {"sheet": "摆臂成本核算", "raw": {"A": "K8057LB", "E": "主板", "G": "KA8057"}}}
    return {"code": code, "kind": "arm", "source": "excel", "source_info": source, "items": [item], **fields}


class HistoricalMaterialPlanTest(unittest.TestCase):
    def test_theory_changes_do_not_replace_real_net_measurements(self):
        ref = reference()
        row = material(length=1120)
        key = material_match_key("８０５７", "　主板  ")
        self.assertEqual(key, ("8057", "主板"))
        changes, matched, reviews = plan_materials([ref], [row, material(id=6, active=0)])
        self.assertEqual(changes, {5: "0.95"})
        self.assertEqual(reviews, [])
        self.assertEqual({k: v for k, v in matched[key].items() if k != "scrap_percent"}, {
            "model": "8057", "code": "KA8057", "id": 5, "name": "主板", "spec": "2.5×357×1260",
            "pieces": "4", "sync_id": "material-1", "gross_weight": "1.961715", "net_weight": "0.95",
            "scrap_weight": "1.011715",
        })
        self.assertIsNone(row["net_weight"])
        self.assertEqual(plan_materials([ref], [{**row, "net_weight": "0.9500"}]), ({}, matched, []))
        changes, matched, reviews = plan_materials([ref], [material(net_weight="0.97")])
        self.assertEqual((changes, matched), ({}, {}))
        self.assertIn("已有不同", reviews[0]["reason"])

    def test_missing_ambiguous_conflicting_or_estimated_rows_always_require_review(self):
        for src, rows in [
            ([reference(name="")], [material(part="")]),
            ([reference(code="8057-1")], [material()]),
            ([reference(name="副班")], [material(part="副板")]),
            ([reference(), reference(net_weight="0.9")], [material()]),
            ([reference()], [material(), material(id=6, code="BB8057")]),
            ([reference(estimated=True)], [material()]),
            ([reference(source_info={"raw": {"E": "=E98"}})], [material()]),
            ([reference(source_info={"raw": {"E": "0.93"}})], [material()]),
            ([reference(net_weight="0")], [material()]),
            ([reference(net_weight="Infinity")], [material()]),
            ([reference(gross_weight="0.5")], [material()]),
            ([reference()], [material(thickness=0.9)]),
            ([reference()], [material(sync_id="")]),
            ([reference()], [material(active=0)]),
            ([reference()], [material(code="KA8057L")]),
        ]:
            with self.subTest(src=src, rows=rows):
                changes, matches, reviews = plan_materials(src, rows)
                self.assertEqual((changes, matches), ({}, {}))
                self.assertEqual(len(reviews), len(src))

    def test_honors_explicit_side_or_exact_part_evidence_without_guessing_prefixes(self):
        key = ("8057", "主板")
        rows = [material(code="BB8057"), material(id=7, code="QA8057", sync_id="7")]
        self.assertEqual(plan_materials([reference()], rows, {key: {"QA8057"}})[0], {7: "0.95"})
        for codes in ({"K8050"}, {"QA8057", "BB8057"}):
            self.assertEqual(plan_materials([reference()], rows, {key: codes})[0], {})
        for name, code, accepted in [("焊接支架L", "KD8228L", True), ("焊接支架右", "KD8228L", False)]:
            with self.subTest(name=name):
                self.assertEqual(bool(plan_materials([reference(name=name)], [material(part=name, code=code)])[0]),
                                 accepted)

    def test_evidence_stays_on_named_references_and_detects_shared_and_handed_conflicts(self):
        ref_key = ("8057", "主板")
        original = record()
        changed_name = record()
        changed_name["items"][0].update(name="托板", product_code="UNRELATED")
        wrong_sheet = record(source_info={"sheet": "球头成本核算"})
        unrelated = record(code="K9011LB")
        peer = record(code="K8057RB", source="copy", pair={"id": "pair", "peer_id": "left"})
        peer["items"][0]["product_code"] = "KA8057R"
        self.assertEqual(material_code_evidence([original, changed_name, wrong_sheet, unrelated]),
                         {ref_key: {"KA8057"}})
        self.assertEqual(material_code_evidence([original, peer]), {ref_key: {"KA8057", "KA8057R"}})

    def test_partial_codes_in_duplicate_rows_cannot_pick_the_first_and_a_conflicting_side_cannot_be_forced(self):
        original = record()
        second = record()["items"][0]
        second["part_code"] = ""
        second["source_info"]["raw"].pop("G")
        original["items"].append(second)
        ambiguous = material_code_evidence([original])
        self.assertEqual(plan_materials([reference()], [material(), material(id=9, code="BB8057")], ambiguous)[0], {})
        wrong_side = ("8057", "托架右")
        self.assertEqual(plan_materials([reference(name=wrong_side[1])], [material(part=wrong_side[1], code="KA8057L")],
                                        {wrong_side: {"KA8057L"}})[0], {})

    def test_workbook_marks_only_estimated_net_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "片件重量"
            sheet.append(["产品编码", "产品名称", "单价", "毛重", "净重"])
            for i in range(2, 6):
                sheet.append(["8057", "主板", 2, 2.19, 0.95])
            sheet["E2"].fill = PatternFill(patternType="solid", fgColor="FFFFFF00")
            sheet["E3"].fill = PatternFill(patternType="solid", fgColor="FFFF0000")
            sheet["B4"].fill = PatternFill(patternType="solid", fgColor="FFFFFF00")
            workbook.save(path)
            workbook.close()
            self.assertEqual(extract_estimated_rows(path), {2})
