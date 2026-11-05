import json
import re
from html import unescape

from tests.web_app_test_base import WebAppTestBase


class TestMaterialCopy(WebAppTestBase):
    def test_reusing_a_renamed_code_allocates_a_new_sync_identity(self):
        self.login()
        fields = {"model": "REUSE-MODEL", "part": "主板", "spec_text": "2.0×300×1000", "pieces": "2", "active": "1"}
        self.client.post("/materials/items/save", data={**fields, "code": "A"})
        with self.web.connect(self.web.DB_PATH) as conn:
            source, = conn.execute("SELECT sync_id FROM material_items WHERE model=? AND code='A'",
                                   (fields["model"],)).fetchone()
        headers = {"Accept": "application/json"}
        copy = {**fields, "code": "B", "copy_from": source}
        self.assertTrue(self.client.post("/materials/items/save", data=copy, headers=headers).json["ok"])
        with self.web.connect(self.web.DB_PATH) as conn:
            previous_id, previous_sync = conn.execute("SELECT id, sync_id FROM material_items WHERE model=? AND code='B'",
                                                      (fields["model"],)).fetchone()
        self.client.post("/materials/items/save", data={**fields, "id": previous_id, "code": "B2"})
        self.assertTrue(self.client.post("/materials/items/save", data=copy, headers=headers).json["ok"])
        self.assertEqual(self.client.post("/materials/items/save", data=copy, headers=headers).status_code, 422)
        with self.web.connect(self.web.DB_PATH) as conn:
            current = dict(conn.execute("SELECT code, sync_id FROM material_items WHERE model=?", (fields["model"],)))
        self.assertEqual(set(current), {"A", "B", "B2"})
        self.assertEqual(current["B2"], previous_sync)
        self.assertEqual(len(set(current.values())), 3)

    def test_copy_keeps_actual_cutting_dimensions_and_creates_an_independent_part(self):
        self.login()
        source = {"model": "COPY-MODEL", "code": "COPY-A", "part": "副板", "category": "冲压",
                  "car": "车型A", "pieces": "3.125", "spec_text": "2.5146×310×1200", "net_weight": "0.5",
                  "active": "1"}
        self.client.post("/materials/items/save", data=source)
        with self.web.connect(self.web.DB_PATH) as conn:
            conn.execute("UPDATE material_items SET spec_text='专用板（关联图号）' WHERE code='COPY-A'")
            original = dict(conn.execute("SELECT * FROM material_items WHERE code='COPY-A'").fetchone())
        page = self.client.get("/materials/items?q=COPY-A").get_data(as_text=True)
        payload, = re.findall(r"data-copy-material='([^']+)'", page)
        draft = json.loads(unescape(payload))
        self.assertEqual(draft, {k: original[k] for k in ("model", "part", "pieces", "spec_text", "category", "car")}
                         | {"copy_from": original["sync_id"]})
        headers = {"Accept": "application/json"}
        form = {**draft, "code": "COPY-B", "active": "1", "net_weight": ""}
        for invalid in ({"code": ""}, {"code": "copy-a"}, {"copy_from": "missing"}, {"id": original["id"]}):
            with self.subTest(invalid=invalid):
                response = self.client.post("/materials/items/save", data=form | invalid, headers=headers)
                self.assertEqual(response.status_code, 422)
        response = self.client.post("/materials/items/save", data=form, headers=headers)
        self.assertTrue(response.json["ok"])
        self.assertIn("COPY-B", self.client.get(response.json["url"]).get_data(as_text=True))
        self.assertEqual(self.client.post("/materials/items/save", data=form, headers=headers).status_code, 422)
        with self.web.connect(self.web.DB_PATH) as conn:
            old, new = [dict(r) for r in conn.execute("SELECT * FROM material_items WHERE model='COPY-MODEL' ORDER BY id")]
            self.assertEqual(old, original)
            for key in ("model", "part", "category", "car", "spec_text", "thickness", "width", "length", "pieces"):
                self.assertEqual(new[key], original[key], key)
            self.assertIsNone(new["net_weight"])
            self.assertNotEqual(new["id"], old["id"])
            self.assertNotEqual(new["sync_id"], old["sync_id"])
        changed = self.client.post("/materials/items/save", data=form | {"code": "COPY-C", "spec_text": "2*310*1200",
                                   "net_weight": "0.6"}, headers=headers)
        self.assertTrue(changed.json["ok"])
        with self.web.connect(self.web.DB_PATH) as conn:
            self.assertEqual(tuple(conn.execute("SELECT spec_text, thickness, net_weight FROM material_items WHERE code='COPY-C'").fetchone()),
                             ("2.0×310×1200", 2, "0.6"))
