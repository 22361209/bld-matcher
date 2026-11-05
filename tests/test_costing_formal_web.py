from __future__ import annotations

import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from openpyxl import Workbook

from tests.web_app_test_base import WebAppTestBase


class CostingFormalWebTest(WebAppTestBase):
    def setUp(self):
        self.web.app.config["TESTING"] = True
        self.client = self.web.app.test_client()
        self.login()
        self.service = self.web.app.extensions["costing_service"]
        self.imports = self.web.app.extensions["costing_imports"]
        self.code = "WEB-" + uuid4().hex[:12].upper()

    def tearDown(self):
        self.web.app.config["TESTING"] = True

    def csrf(self, client=None):
        page = (client or self.client).get("/costs/new").get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def payload(self, **extra):
        return {"code": self.code, "kind": "arm", "name": "合成成本",
                "items": [{"name": "材料", "amount": "100"}], **extra}

    def post(self, path, data, *, client=None, token=None):
        return (client or self.client).post(path, json=data, headers={
            "Accept": "application/json", "X-CSRF-Token": token or self.csrf(client),
        })

    def viewer(self, *, cost_access=False):
        from app.modules.admin.factory import get_admin_service

        admin = get_admin_service()
        with self.client.session_transaction() as state:
            admin_id = state["user_id"]
        username = "cost-view-" + uuid4().hex[:8]
        admin.save_user({"username": username, "display_name": username, "password": "viewer-password",
                         "role": "viewer", "active": "1",
                         "permission_overrides": {"view_costs": "allow"} if cost_access else {}},
                        actor="007", actor_user_id=admin_id)
        client = self.web.app.test_client()
        response = client.post("/login", data={"username": username, "password": "viewer-password"})
        self.assertEqual(response.status_code, 302)
        return client

    def test_formal_navigation_actions_and_shared_editor_use_main_endpoints(self):
        page = self.client.get("/costs")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        navigation = re.search(r"<nav\b.*?</nav>", body, re.S).group(0)
        self.assertIn('href="/costs"', navigation)
        self.assertIn("成本核算", navigation)
        self.assertNotIn("导入核对", navigation)
        self.assertNotIn("独立试用", navigation)
        self.assertIn('href="/costs/import"', body)
        self.assertIn('href="/costs/new"', body)
        references = {"cartons": [{"name": "54*41*15.5"}], "rates": [{"name": "电泳"}]}
        with patch.object(self.imports, "references", return_value=references):
            editor = self.client.get("/costs/new").get_data(as_text=True)
        self.assertIn('data-preview-url="/costs/preview"', editor)
        self.assertIn('data-list-url="/costs"', editor)
        self.assertIn('form="costing-editor" data-costing-leave-save', editor)
        self.assertIn('action="/costs/new"', editor)
        self.assertIn('data-costing-identity-toggle aria-expanded="true"', editor)
        kinds = re.search(r'<select name="kind"[^>]*>(.*?)</select>', editor, re.S).group(1)
        self.assertEqual(re.findall(r'<option value="([^"]+)"( selected)?>([^<]+)</option>', kinds),
                         [("arm", " selected", "控制臂"), ("ball", "", "悬挂球头")])
        self.assertIn('data-costing-description>控制臂</span>', editor)
        self.assertEqual(re.findall(r'<th scope="col" data-col="([^"]+)">', editor),
                         ["row", "category", "name", "product_code", "supplier", "material_attribute", "mode", "price", "usage", "total", "details"])
        options = re.search(r'data-material-attributes="([^"]+)"', editor).group(1)
        self.assertEqual(json.loads(unescape(options)), ["自制", "外购", "委外加工"])
        choices = json.loads(unescape(re.search(r'data-references="([^"]+)"', editor).group(1)))
        self.assertEqual(choices["cartons"][0]["material_attribute"], "外购")
        self.assertEqual(choices["rates"][0]["material_attribute"], "委外加工")
        template_attributes = {row["name"]: row["material_attribute"]
                               for rows in choices["templates"].values() for row in rows}
        self.assertEqual([template_attributes[name] for name in ("主板", "球座", "电泳")], ["自制", "外购", "委外加工"])
        self.assertNotIn("data-costing-add", editor)
        descending = self.client.get("/costs?sort=code_desc").get_data(as_text=True)
        self.assertIn('<option value="code_desc" selected>', descending)
        imported = self.client.get("/costs/import").get_data(as_text=True)
        self.assertIn('action="/costs/import/preview"', imported)

    def test_scrap_detail_distinguishes_applied_credit_and_original_piece_evidence(self):
        source = {
            "pivot": {"sheet": "透视表", "row": 15, "raw": {"A": "K1000LA", "F": "1.5", "K": "=SUM(H4:H8)"},
                      "cached": {"K": "2.25"}},
            "scrap_known_total": "2.25",
            "scrap_reference_rows": [
                {"name": "主板", "gross_weight": "2", "net_weight": "1.25", "scrap_weight": "0.75",
                 "unit_price": "1.6", "amount": "2.25", "source_info": {
                     "sheet": "片件重量", "row": 7, "raw": {"F": "=D7-E7", "H": "=F7*C5"}}},
                {"name": "副板", "amount": None, "review_note": "重量或价格资料不完整"},
            ],
        }
        imported = self.service.create(self.payload(scrap_credit="1.8", theoretical_scrap="2.2", source_info=source),
                                       source="copy", actor="007")
        body = self.client.get(f"/costs/{imported['id']}").get_data(as_text=True)
        self.assertIn('aria-controls="costing-scrap-detail" aria-expanded="false">计算明细</button>', body)
        detail = body.split('<section id="costing-scrap-detail"', 1)[1].split("</section>", 1)[0]
        for value in ("原表型号</strong> K1000LA", "透视表!F15 · 1.5 元/件", "透视表!K15 · 2.25 元/件",
                      "=SUM(H4:H8)", "=F7*C5", "片件重量!H7", "2.25 元/件", "还有 1 行缺少金额", "重量或价格资料不完整"):
            self.assertIn(value, detail)
        self.assertIn('data-costing-total="scrap_credit"', detail)
        self.assertRegex(detail, r'name="theoretical_scrap"[^>]*value="2.2"')

        manual = self.client.get("/costs/new").get_data(as_text=True)
        self.assertIn("本单未附废料来源明细", manual)
        self.assertNotIn("已知片件小计", manual)
        self.assertEqual(manual.count('name="theoretical_scrap"'), 1)

        missing = self.service.create(self.payload(code=self.code + "-missing", source_info={
            "scrap_reference_rows": [{"name": "片件", "amount": None}], "scrap_known_total": "0"}), actor="007")
        detail = self.client.get(f"/costs/{missing['id']}").get_data(as_text=True)
        self.assertIn("已知片件小计　— 元/件", detail)
        self.assertIn("还有 1 行缺少金额", detail)

    def test_formal_login_permissions_and_read_only_costs(self):
        anon = self.web.app.test_client()
        self.assertEqual(anon.get("/costs", headers={"Accept": "application/json"}).status_code, 401)
        self.assertIn("/login", anon.get("/costs").location)
        record = self.service.create(self.payload(), actor="007")
        denied = self.viewer()
        self.assertEqual(denied.get("/costs", headers={"Accept": "application/json"}).status_code, 403)
        self.assertNotIn('href="/costs"', denied.get("/products").get_data(as_text=True))
        viewer = self.viewer(cost_access=True)
        body = viewer.get(f"/costs/{record['id']}").get_data(as_text=True)
        self.assertIn("readonly", body)
        self.assertNotIn("保存成本", body)
        self.assertNotIn("data-costing-leave-dialog", body)
        self.assertIn('data-costing-identity-toggle aria-expanded="false"', body)
        self.assertIn('<th scope="col" data-col="details">', body)
        self.assertNotIn('<th scope="col" data-col="actions">', body)
        self.assertEqual(viewer.get("/costs/import", headers={"Accept": "application/json"}).status_code, 403)
        self.assertEqual(viewer.post("/costs/new", json=self.payload(),
                                     headers={"Accept": "application/json"}).status_code, 403)

    def test_cost_list_vehicle_heading_type_labels_and_existing_filter_values(self):
        types = (("arm", "控制臂", "雅绅特"), ("ball", "悬挂球头", "伊兰特"))
        for kind, _, model in types:
            self.service.create(self.payload(code=f"{self.code}-{kind}", kind=kind, models=model), actor="007")

        def listing(**filters):
            response = self.client.get("/costs", query_string={"q": self.code, **filters})
            self.assertEqual(response.status_code, 200)
            return response.get_data(as_text=True)

        body = listing()
        self.assertIn('data-col="name" data-column-label="车型"', body)
        dropdown = re.search(r'<select name="kind">(.*?)</select>', body, re.S).group(1)
        rows = re.findall(r"<tr>(.*?)</tr>", body.split("<tbody>")[1].split("</tbody>")[0], re.S)
        self.assertEqual(len(rows), 2)
        for kind, label, model in types:
            row = next(row for row in rows if model in row)
            self.assertIn("合成成本", row)
            self.assertIn(f'<td data-col="kind">{label}</td>', row)
            self.assertRegex(dropdown, rf'<option value="{kind}"\s*>{label}</option>')
            self.assertRegex(body, rf'type="checkbox" value="{kind}" checked><span>{label}</span>')
        arm, ball = listing(kind="arm"), listing(cf_kind="ball")
        for filtered, label in ((arm, "控制臂"), (ball, "悬挂球头")):
            self.assertEqual(re.findall(r'<td data-col="kind">(.*?)</td>', filtered), [label])
        self.assertIn('<option value="arm" selected>控制臂</option>', arm)
        self.assertIn('value="ball" checked><span>悬挂球头</span>', ball)
        self.assertRegex(ball, r'value="arm"\s*><span>控制臂</span>')

    def test_real_csrf_and_server_actor_price_and_conflict(self):
        token = self.csrf()
        self.web.app.config["TESTING"] = False
        denied = self.client.post("/costs/new", json=self.payload(), headers={"Accept": "application/json"})
        self.assertEqual(denied.status_code, 400)
        created = self.post("/costs/new", self.payload(actor="spoof", created_by="spoof"), token=token)
        self.assertEqual(created.status_code, 201)
        record = created.get_json()["record"]
        self.assertEqual((record["created_by"], record["minimum_tax_price"]), ("007", "137.5"))
        self.assertEqual(created.get_json()["url"], f"/costs/{record['id']}")
        response = self.post(f"/costs/{record['id']}", {"version": 1, "suggested_tax_price_override": "145",
                                                       "actor": "spoof"}, token=token)
        self.assertEqual(response.status_code, 200)
        updated = response.get_json()["record"]
        self.assertEqual((updated["updated_by"], updated["suggested_tax_price"]), ("007", "145"))
        self.assertEqual(self.post(f"/costs/{record['id']}", {"version": 1}, token=token).status_code, 409)
        invalid = self.post("/costs/preview", self.payload(suggested_tax_price_override="-1"), token=token)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["field"], "suggested_tax_price_override")
        self.assertEqual(self.client.get("/costs/missing", headers={"Accept": "application/json"}).status_code, 404)

    def test_reverted_amount_previews_and_saves_historical_costs_with_current_prices(self):
        # Seed an older sheet whose recorded cost and pricing rule differ from today's.
        payload = self.payload()
        historical = self.service.calculate(payload)
        historical["items"][0]["line_total"] = "64.00"
        historical.update(known_subtotal="64.00", calculated_cost="64.00", effective_cost="64.00",
                          net_cost="64.00", minimum_tax_price="80", suggested_tax_price="80")
        with patch("app.modules.costing.service.calculate_cost", return_value=historical):
            stored = self.service.create(payload, source="excel", actor="原导入人")

        token = self.csrf()
        self.web.app.config["TESTING"] = False
        url = f"/costs/{stored['id']}"
        line = {"id": stored["items"][0]["id"], "name": "更新后的项目名称", "mode": "fixed"}
        draft = {"id": stored["id"], "code": self.code, "version": 1}

        def amounts(record):
            return (record["items"][0]["line_total"], record["known_subtotal"], record["calculated_cost"],
                    record["effective_cost"], record["net_cost"], record["minimum_tax_price"],
                    record["suggested_tax_price"])

        copy_page = self.client.get(f"/costs/new?copy={stored['id']}").get_data(as_text=True)
        copied = json.loads(unescape(re.search(r'data-record="([^"]+)"', copy_page).group(1)))
        self.assertEqual(amounts(copied), ("100", "100", "100", "100", "100", "137.5", "137.5"))
        expected = ("64.00", "64.00", "64.00", "64.00", "64.00", "88", "88")
        for entered, totals in (("120", ("120", "120", "120", "120", "120", "165", "165")),
                                ("100", expected), ("100.000", expected)):
            with self.subTest(entered=entered):
                draft["items"] = [{**line, "amount": entered}]
                preview = self.post("/costs/preview", draft, token=token)
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(amounts(preview.get_json()["record"]), totals)

        before_save = self.client.get(url, headers={"Accept": "application/json"}).get_json()["record"]
        self.assertEqual((before_save["version"], before_save["items"][0]["name"]), (1, "材料"))
        saved = self.post(url, draft, token=token)
        self.assertEqual(saved.status_code, 200)
        reopened = self.client.get(url, headers={"Accept": "application/json"}).get_json()["record"]
        self.assertEqual(saved.get_json()["record"], reopened)
        self.assertEqual(amounts(reopened), expected)
        self.assertEqual((reopened["version"], reopened["items"][0]["name"]), (2, line["name"]))
        self.assertEqual(self.post("/costs/preview", draft, token=token).status_code, 409)

    def test_line_product_code_is_independent_and_preserved_for_older_editors(self):
        line = {"name": "外箱", "amount": "6.2", "part_code": "BOX-A"}
        original = self.post("/costs/new", self.payload(items=[line])).get_json()["record"]
        url = f"/costs/{original['id']}"
        old_line = {**line, "id": original["items"][0]["id"]}
        self.assertFalse(original["items"][0].get("product_code"))
        for version, fields, expected in (
            (1, {"product_code": "0007-a"}, "0007-a"),
            (2, {}, "0007-a"),
            (3, {"product_code": ""}, ""),
        ):
            response = self.post(url, {"version": version, "items": [{**old_line, **fields}]})
            self.assertEqual(response.status_code, 200)
            item = self.client.get(url, headers={"Accept": "application/json"}).get_json()["record"]["items"][0]
            self.assertEqual((item["product_code"], item["part_code"], item["line_total"]),
                             (expected, "BOX-A", "6.2"))

    def test_material_attribute_optional_saved_copied_and_compatible_with_old_editor(self):
        line = {"name": "主板", "amount": "9.855", "product_code": "P-21"}
        original = self.post("/costs/new", self.payload(items=[line])).get_json()["record"]
        self.assertEqual(original["items"][0]["material_attribute"], "自制")
        line["id"] = original["items"][0]["id"]
        url = f"/costs/{original['id']}"
        for version, fields, expected in (
            (1, {"material_attribute": "自制"}, "自制"),
            (2, {"material_attribute": "外购"}, "外购"),
            (3, {"material_attribute": "委外加工"}, "委外加工"),
            (4, {}, "委外加工"),
        ):
            saved = self.post(url, {"version": version, "items": [{**line, **fields}]})
            self.assertEqual(saved.status_code, 200)
            item = self.client.get(url, headers={"Accept": "application/json"}).get_json()["record"]["items"][0]
            self.assertEqual((item["material_attribute"], item["product_code"], item["line_total"]), (expected, "P-21", "9.855"))
        invalid = self.post(url, {"version": 5, "items": [{**line, "material_attribute": "其他"}]})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["field"], "items.0.material_attribute")
        copy_page = self.client.get(f"/costs/new?copy={original['id']}").get_data(as_text=True)
        copy_record = json.loads(unescape(re.search(r'data-record="([^"]+)"', copy_page).group(1)))
        copy_record["code"] = self.code + "-COPY"
        copy = self.post("/costs/new", copy_record).get_json()["record"]
        self.assertEqual(copy["items"][0]["material_attribute"], "委外加工")
        cleared = self.post(url, {"version": 5, "items": [{**line, "material_attribute": ""}]})
        self.assertEqual(cleared.get_json()["record"]["items"][0]["material_attribute"], "")
        self.assertEqual(self.service.get(copy["id"])["items"][0]["material_attribute"], "委外加工")

    def test_legacy_missing_material_attribute_stays_blank_and_new_row_defaults_to_in_house(self):
        legacy = self.service.create(self.payload(), actor="import", source="excel")
        old_item = legacy["items"][0]
        self.assertNotIn("material_attribute", old_item)
        url = f"/costs/{legacy['id']}"
        saved = self.post(url, {"version": 1, "items": [old_item, {"name": "主板", "amount": "1"}]})
        old_item, added = saved.get_json()["record"]["items"]
        self.assertFalse(old_item.get("material_attribute"))
        self.assertEqual(added["material_attribute"], "自制")
        copy_page = self.client.get(f"/costs/new?copy={legacy['id']}").get_data(as_text=True)
        copy_record = json.loads(unescape(re.search(r'data-record="([^"]+)"', copy_page).group(1)))
        self.assertEqual([item["material_attribute"] for item in copy_record["items"]], ["", "自制"])

    def preview_workbook(self, client=None):
        stream = io.BytesIO()
        Workbook().save(stream)
        stream.seek(0)
        parsed = {"records": [self.payload()], "warnings": [], "references": {}}
        with patch("app.modules.costing.workbook_import.parse_cost_workbook", return_value=parsed):
            response = (client or self.client).post("/costs/import/preview",
                data={"file": (stream, "costing.xlsx")}, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["preview_token"]

    def test_import_is_bound_to_user_and_login_session_and_single_use(self):
        token = self.preview_workbook()
        another = self.web.app.test_client()
        another.post("/login", data={"username": "007", "password": "test-admin-pw"})
        denied = self.post("/costs/import/apply", {"preview_token": token}, client=another)
        self.assertEqual(denied.status_code, 422)
        result = self.post("/costs/import/apply", {"preview_token": token})
        self.assertEqual(result.status_code, 200)
        imported = self.service.list(q=self.code)["records"][0]
        self.assertEqual(imported["created_by"], "007")
        self.assertEqual(self.post("/costs/import/apply", {"preview_token": token}).status_code, 409)
        self.assertTrue(self.imports.directory.resolve().is_relative_to((self.root / "uploads").resolve()))

    def test_concurrent_workers_claim_one_import_and_return_conflict_to_loser(self):
        token = self.preview_workbook()
        with self.client.session_transaction() as session:
            state = session["costing_import_owner"]
            owner = f"{state['user']}:{state['nonce']}"
        from app.modules.costing.domain import CostingConflict
        from app.modules.costing.trial_import_service import TrialImportService

        worker = TrialImportService(self.imports.directory, str(self.web.app.secret_key), self.service)
        identifier = self.imports.signer.loads(token)["id"]
        preview_path = self.imports.preview_dir / f"{identifier}.json"
        rename = Path.replace
        barrier = Barrier(2)

        def concurrent_rename(source, target):
            if source == preview_path:
                barrier.wait(timeout=5)
            return rename(source, target)

        def apply(importer):
            try:
                return importer.apply(token, owner=owner, actor="007")["imported"]
            except CostingConflict:
                return "conflict"

        with patch.object(Path, "replace", concurrent_rename), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(apply, [self.imports, worker]))
        self.assertCountEqual(results, [1, "conflict"])
        self.assertEqual(self.service.list(q=self.code)["total"], 1)
