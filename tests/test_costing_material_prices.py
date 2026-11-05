from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.database import connect
from app.modules.costing.domain import CostingConflict
from app.modules.costing.material_prices import MaterialPriceService
from app.modules.costing.service import CostingService
from app.modules.costing.transfer_service import CostingTransferService
from app.modules.materials.item_store import upsert_material_item
from tests.web_app_test_base import WebAppTestBase


class StampingPricesTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "test.sqlite3"
        self.service = CostingService(self.path)
        self.prices = MaterialPriceService(self.service)
        self.conn = connect(self.path)
        self.addCleanup(self.conn.close)
        self.base = {"model": "8057", "part": "主板", "spec_text": "2.5×357×1260", "pieces": "4"}
        self.change = self.prices.request("SPHC", "5.123")

    def material(self, code, **kwargs):
        item_id = upsert_material_item(self.conn, {**self.base, "code": code, **kwargs})
        self.conn.commit()
        return item_id

    @staticmethod
    def weight(code, **kwargs):
        return {"name": "主板", "product_code": code, "mode": "weight", "unit_price": "3",
                "processing_fee": "1", "quantity": "2", "weight": "4", **kwargs}

    def sheet(self, code, items, **kwargs):
        return self.service.create({"code": code, "items": items, **kwargs}, actor="制表员")

    def test_one_grade_crosses_component_codes_and_preserves_other_charges_and_overrides(self):
        self.material("P1")
        self.material("P2", part="副板")
        self.material("P440")
        first = self.sheet("K8057LA", [self.weight("p1"), self.weight("P440"), self.weight("MISSING440"),
                                      self.weight("P2", name="焊接管"), self.weight("P2", mode="unit"),
                                      self.weight("P2", mode="freight")],
                           approved_cost="80", scrap_credit="2", suggested_tax_price_override="121")
        pair = self.sheet("K8070LA", [self.weight("P2", name="副板")], create_pair=True)
        preview = self.prices.preview(self.change)
        self.assertEqual((preview["updated_models"], preview["updated_rows"]), (3, 3))
        self.assertEqual(len(preview["reviews"]), 1)
        self.assertEqual(preview["reviews"][0]["reason"], "材料明细中没有此编码")
        self.assertEqual(self.service.get(first["id"])["version"], 1)
        self.prices.apply(self.change, preview["digest"], actor="采购")
        record = self.service.get(first["id"])
        self.assertEqual(record["items"][0]["line_total"], "42.984")
        self.assertEqual((record["items"][1:], record["approved_cost"], record["net_cost"],
                          record["suggested_tax_price"]), (first["items"][1:], "80", "78", "121"))
        left = self.service.get(pair["id"])
        right = self.service.get(left["pair"]["peer_id"])
        self.assertEqual((left["items"][0]["line_total"], left["updated_at"]), ("42.984", right["updated_at"]))
        self.assertEqual(right["items"][0]["unit_price"], "5.123")
        rev = self.service.revisions(pair["id"])[0]
        self.assertEqual((rev["actor"], rev["before"]["items"][0]["unit_price"]), ("采购", "3"))
        snapshot = CostingTransferService(self.path).export_snapshot()
        self.assertEqual(len(snapshot["revisions"]), 6)
        with self.assertRaises(CostingConflict):
            self.prices.apply(self.change, preview["digest"], actor="采购")

    def test_stale_master_or_cost_version_and_auditing_failure_roll_back_every_sheet(self):
        material = self.material("P")
        a, b = [self.sheet(name, [self.weight("P")]) for name in ("A", "B")]
        before = self.prices.preview(self.change)
        self.material("P440", id=material)
        with self.assertRaises(CostingConflict):
            self.prices.apply(self.change, before["digest"], actor="采购")
        self.material("P", id=material)
        self.service.update(b["id"], {"remark": "核对"}, expected_version=1, actor="同事")
        with self.assertRaises(CostingConflict):
            self.prices.apply(self.change, before["digest"], actor="采购")
        preview = self.prices.preview(self.change)
        save = self.service._save_edit
        def fail_second(repo, old, new, actor):
            if old["code"] == "B":
                raise RuntimeError("模拟第二张修订写入失败")
            save(repo, old, new, actor)
        with patch.object(self.service, "_save_edit", side_effect=fail_second), self.assertRaises(RuntimeError):
            self.prices.apply(self.change, preview["digest"], actor="采购")
        self.assertEqual(self.service.get(a["id"])["version"], 1)
        self.assertEqual(self.service.get(b["id"])["version"], 2)

    def test_verified_stamping_source_distinguishes_duplicate_or_inactive_materials(self):
        from app.modules.materials.item_store import get_material_item
        m1 = self.material("C")
        self.material("C")
        source = {"kind": "stamping", "product_code": "C", "name": "主板", "model": "8057",
                  "spec": "2×30×100", "source_id": get_material_item(self.conn, m1)["sync_id"], "pieces": "1",
                  "gross_weight": "4", "net_weight": "3", "scrap_weight": "1"}
        stamp = self.sheet("K8057LA", [self.weight("C", weight_source=source)])
        self.sheet("K8057RB", [self.weight("C", weight_source={**source, "kind": "tube",
                   "pieces": None, "net_weight": None, "scrap_weight": None})])
        preview = self.prices.preview(self.change)
        self.assertEqual(preview["updated_rows"], 1)
        self.assertEqual(preview["details"][0]["rows"][0]["evidence"], "已确认取重来源")
        self.material("C", id=m1, active="0")
        with self.assertRaises(CostingConflict):
            self.prices.apply(self.change, preview["digest"], actor="采购")
        revised = self.prices.preview(self.change)
        self.assertEqual(revised["updated_rows"], 0)
        self.assertEqual([(r["id"], r["reason"]) for r in revised["reviews"]], [(stamp["id"], "原取重材料已停用或不存在")])

    def test_handed_different_grades_and_ambiguous_history_are_reviewed(self):
        self.material("C8057L")
        self.material("C440R")
        self.material("DUP")
        self.material("DUP")
        self.material("GEN", model="8058", part="支架")
        left = self.sheet("K8057LA", [self.weight("C8057L", peer_product_code="C440R")], create_pair=True)
        self.sheet("K8057LB", [self.weight("DUP")])
        self.sheet("K8058RA", [self.weight("", name="支架")])
        self.sheet("K8058B", [self.weight("", name="支架")])  # No left/right suffix.
        preview = self.prices.preview(self.change)
        self.assertEqual((preview["updated_models"], preview["updated_rows"]), (2, 2))
        self.assertIn("SAPH440", preview["blocked"][0]["reason"])
        self.assertEqual(preview["details"][0]["rows"][0]["evidence"], "母件及零件名称唯一对应")
        self.assertIn("多条", preview["reviews"][0]["reason"])
        self.prices.apply(self.change, preview["digest"], actor="采购")
        self.assertEqual(self.service.get(left["id"])["version"], 1)
        self.assertEqual(self.service.get(left["pair"]["peer_id"])["version"], 1)

    def test_distinct_grades_within_one_parent_and_unmatched_or_conflicting_codes(self):
        self.material("PLAIN", model="8440", material_grade="SAPH440")
        self.material("P440", model="8440", material_grade="SPHC")
        self.material("P４４０", model="8440")
        self.material("OTHER４４０", model="8440")
        record = self.sheet("K8440LA", [self.weight("", part_code="PLAIN"), self.weight("P440"),
                            self.weight("P４４０"), self.weight("OTHER440"), self.weight("P440", part_code="PLAIN")])
        catalog = self.prices.catalog()
        self.assertEqual(dict(catalog["counts"]), {"SPHC": 2, "SAPH440": 1})
        self.assertEqual([(r["position"], r["reason"]) for r in catalog["reviews"]], [
            (4, "材料明细中没有此编码"), (5, "行内产品编码与零件编码不同，请确认取重来源"),
        ])
        change = self.prices.request("SAPH440", "9")
        preview = self.prices.preview(change)
        self.assertEqual([r["position"] for r in preview["details"][0]["rows"]], [2])
        self.prices.apply(change, preview["digest"], actor="采购")
        self.assertEqual([r["unit_price"] for r in self.service.get(record["id"])["items"]], ["3", "9", "3", "3", "3"])

    def test_matched_material_with_invalid_grade_is_distinct_from_missing_material(self):
        self.sheet("K8057LA", [self.weight("BROKEN"), self.weight("MISSING")])
        material = {"sync_id": "legacy", "code": "BROKEN", "model": "8057", "part": "主板", "material_grade": None}
        with self.prices._work(False) as (uow, _):
            matches, reviews, _ = self.prices._matches(uow.repository, [material])
        self.assertFalse(matches)
        self.assertEqual([r["reason"] for r in reviews], ["已匹配材料明细，牌号缺失或无效", "材料明细中没有此编码"])


class StampingPriceWebTest(WebAppTestBase):
    path = "/costs/material-prices"
    def test_authenticated_preview_csrf_signed_confirmation_and_real_write(self):
        from app.modules.admin.factory import get_admin_service
        from app.modules.materials.factory import get_material_service

        self.login()
        self.web.app.config["TESTING"] = False
        self.addCleanup(self.web.app.config.update, TESTING=True)
        material = {"code": "WEB-STEEL", "model": "5010", "part": "主板", "spec_text": "2×100×100",
                    "pieces": "1", "material_grade": "SPHC"}
        get_material_service().save_item(material, actor="007")
        costs = self.web.app.extensions["costing_service"]
        record = costs.create({"code": "WEB-5010", "items": [StampingPricesTest.weight("WEB-STEEL")]}, actor="007")
        self.assertIn(f'href="{self.path}"', self.client.get("/costs").get_data(as_text=True))
        page = self.client.get(self.path).get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)[1]
        data = {"grade": "SPHC", "price": "7"}
        self.assertEqual(self.client.post(self.path+"/preview", data=data, headers={"Accept": "application/json"}).status_code, 400)
        data["csrf_token"] = csrf
        preview = self.client.post(self.path+"/preview", data=data)
        self.assertEqual(preview.status_code, 200)
        html = preview.get_data(as_text=True)
        self.assertIn("WEB-5010", html)
        token = re.search(r'name="token" value="([^"]+)"', html)[1]
        headers = {"Accept": "application/json"}
        self.assertEqual(self.client.post(self.path+"/apply", data={"csrf_token": csrf, "token": "invalid"+token}, headers=headers).status_code, 422)
        applied = self.client.post(self.path+"/apply", data={"csrf_token": csrf, "token": token}, headers=headers)
        self.assertEqual(applied.get_json(), {"ok": True, "rows": 1, "models": 1})
        self.assertEqual(costs.get(record["id"])["items"][0]["unit_price"], "7")
        self.assertEqual(self.client.post(self.path+"/apply", data={"csrf_token": csrf, "token": token}, headers=headers).status_code, 409)
        with self.client.session_transaction() as state:
            admin_id = state["user_id"]
        get_admin_service().save_user({"username": "material-price-viewer", "display_name": "只读", "password": "test-viewer-pw",
                                       "role": "viewer", "active": "1",
                                       "permission_overrides": {"view_costs": "allow", "view_cost_details": "allow"}},
                                      actor="007", actor_user_id=admin_id)
        viewer = self.web.app.test_client()
        login_csrf = re.search(r'name="csrf_token" value="([^"]+)"', viewer.get("/login").get_data(as_text=True))[1]
        viewer.post("/login", data={"username": "material-price-viewer", "password": "test-viewer-pw", "csrf_token": login_csrf})
        self.assertNotIn(f'href="{self.path}"', viewer.get("/costs").get_data(as_text=True))
        self.assertEqual(viewer.get(self.path, headers=headers).status_code, 403)
