from __future__ import annotations

import re

from tests.web_app_test_base import WebAppTestBase


class MaterialGradeWebTests(WebAppTestBase):
    def test_part_code_sets_grade_in_list_filters_edits_and_copies(self):
        from app.modules.materials.factory import get_material_service

        self.login()
        service = get_material_service()
        base = {"model": "GRADE-MODEL-440", "part": "副板", "pieces": "2", "spec_text": "2*200*300", "active": "1"}
        for code, supplied in (("A-443", "SAPH440"), ("B-440-2", "SPHC")):
            # Older forms still send a grade, even when the code field has been edited.
            response = self.client.post("/materials/items/save", data={**base, "code": code, "material_grade": supplied})
            self.assertEqual(response.status_code, 302)
        common = {"q": base["model"]}
        listing = self.client.get("/materials/items", query_string=common).get_data(as_text=True)
        self.assertEqual(re.findall(r'<td data-col="material-grade">(.*?)</td>', listing), ["SPHC", "SAPH440"])
        self.assertNotIn("待确认", listing)
        self.assertNotIn("全选本页", listing)
        self.assertNotIn('name="material_grade"', listing)
        narrowed = self.client.get("/materials/items", query_string={**common, "mf_material_grade": "SAPH440"})
        self.assertEqual(re.findall(r'<td data-col="material-grade">(.*?)</td>', narrowed.get_data(as_text=True)), ["SAPH440"])
        first, second = service.list_items(query=base["model"], status="all", limit=50, offset=0).records
        for path in ("/materials/items/new", f"/materials/items/{first['id']}/edit"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn("零件编码含 440", html)
            self.assertNotIn('name="material_grade"', html)
        self.client.post("/materials/items/save", data={**base, "id": first["id"], "original_sync_id": first["sync_id"],
                                                       "code": "A-440-NEW", "material_grade": "SPHC"})
        self.assertEqual(service.get_item(first["id"])["material_grade"], "SAPH440")
        copied = self.client.post("/materials/items/save", data={**base, "copy_from": second["sync_id"],
                                  "code": "C-44", "material_grade": "SAPH440"}, headers={"Accept": "application/json"})
        self.assertTrue(copied.json["ok"])
        self.assertEqual(service.list_items(query="C-44", status="active", limit=50, offset=0).records[0]["material_grade"],
                         "SPHC")

    def test_manual_registration_urls_are_removed_but_cost_price_preview_remains(self):
        self.login()
        for op in ("preview", "apply"):
            response = self.client.post("/materials/items/grades/" + op, data={"grade": "SAPH440"})
            self.assertEqual(response.status_code, 404)
        page = self.client.get("/costs/material-prices")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn('action="/costs/material-prices/preview"', body)
        self.assertIn("查看材料明细", body)
        self.assertNotIn("维护材料牌号", body)
