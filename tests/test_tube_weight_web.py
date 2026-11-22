from __future__ import annotations

from decimal import Decimal
from html.parser import HTMLParser
from uuid import uuid4

from tests.web_app_test_base import WebAppTestBase


class FormFields(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.fields = {}
        self.options = {}
        self.select = None
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"input", "textarea", "select"} and "name" in attributes:
            self.fields.setdefault(attributes["name"], []).append(attributes)
        if tag == "select":
            self.select = attributes.get("name")
        elif tag == "option" and self.select:
            self.options.setdefault(self.select, []).append(attributes)

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None

    def field(self, name):
        return self.fields[name][0]

    def selected(self, name):
        return [option["value"] for option in self.options[name] if "selected" in option]


class TubeWeightWebTest(WebAppTestBase):
    """Exercise the real routes and services against WebAppTestBase's guarded fixture."""

    def setUp(self):
        from app.modules.tubes.factory import get_tube_service

        self.web.app.config["TESTING"] = True
        self.client = self.web.app.test_client()
        self.login()
        self.tubes = get_tube_service()
        self.code = "TUBE-WEB-" + uuid4().hex[:12].upper()

    def tearDown(self):
        self.web.app.config["TESTING"] = True

    def payload(self, **extra):
        return {
            "code": self.code, "tube_type": "普通管", "spec_text": "Φ41×Φ34.5×41.5",
            "weight_mode": "auto", "weight_kg": "999", "outer_diameter_mm": "41",
            "inner_diameter_mm": "34.5", "weight_length_mm": "41.5",
            "tolerance_mm": "0.25", "consumption_mm": "3", "purchase_base": "8",
            "blank_length_text": "规格中的旧长度不参与计重", "active": "1", **extra,
        }

    def records(self, code=None):
        return self.tubes.list_items(filters={"query": code or self.code}, limit=100, offset=0)["records"]

    def csrf(self):
        return FormFields(self.client.get("/tubes/new").get_data(as_text=True)).field("csrf_token")["value"]

    def lookup(self, code):
        response = self.client.get("/costs/weight-lookup", query_string={"code": code, "kind": "tube"})
        self.assertEqual(response.status_code, 200)
        return response.get_json()["candidates"]

    def test_new_form_defaults_to_auto_without_guessing_length(self):
        response = self.client.get("/tubes/new")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        fields = FormFields(body)
        self.assertIn("新增管件", body)
        self.assertEqual(fields.selected("weight_mode"), ["auto"])
        self.assertEqual(fields.field("weight_length_mm")["value"], "")
        self.assertEqual(fields.field("weight_kg")["value"], "")
        self.assertIn("readonly", fields.field("weight_kg"))
        self.assertIn("mm", body)

    def test_legacy_manual_edit_keeps_recorded_weight_and_blank_length(self):
        item_id = self.tubes.save({"code": self.code, "tube_type": "普通管", "spec_text": "18×2×50",
                                   "weight_kg": "0.1052"}, actor="fixture")
        response = self.client.get(f"/tubes/{item_id}/edit")
        self.assertEqual(response.status_code, 200)
        fields = FormFields(response.get_data(as_text=True))
        self.assertEqual(fields.selected("weight_mode"), ["manual"])
        self.assertEqual(fields.field("weight_kg")["value"], "0.1052")
        self.assertEqual(fields.field("weight_length_mm")["value"], "")
        self.assertNotIn("readonly", fields.field("weight_kg"))

    def test_kg6028_invalid_borrowing_restores_manual_weight_on_error(self):
        precision = "0.123456789012345"
        code = "kg6028"
        item_id = self.tubes.save(self.payload(code=code, weight_mode="manual", weight_kg=precision), actor="fixture")
        before = self.tubes.get_item(item_id)
        submitted = self.payload(id=str(item_id), code=code, weight_mode="manual", weight_kg="",
                                 borrowed_from=self.code + "-MISSING", note="借用失败后继续编辑")
        rejected = self.client.post("/tubes/save", data=submitted)
        self.assertEqual(rejected.status_code, 400)
        body = rejected.get_data(as_text=True)
        self.assertIn("借用来源编号不存在", body)
        fields = FormFields(body)
        self.assertEqual(fields.field("weight_kg")["value"], precision)
        self.assertEqual(fields.field("borrowed_from")["value"], submitted["borrowed_from"])
        self.assertEqual(self.tubes.get_item(item_id), before)

        corrected = {**submitted, "borrowed_from": "", "weight_kg": fields.field("weight_kg")["value"]}
        saved = self.client.post("/tubes/save", data=corrected)
        self.assertEqual(saved.status_code, 302)
        current = self.tubes.get_item(item_id)
        self.assertEqual((current["weight_mode"], current["borrowed_from"], current["weight_kg"]),
                         ("manual", "", float(precision)))
        self.assertEqual(current["note"], submitted["note"])

    def test_kg6028_rejects_auto_case_insensitively_and_preserves_manual_precision(self):
        precision = "0.123456789012345"
        item_id = self.tubes.save(self.payload(code="KG6028", weight_mode="manual", weight_kg=precision), actor="fixture")
        before = self.tubes.get_item(item_id)
        for code in ("KG6028", "kg6028", "Kg6028"):
            with self.subTest(code=code):
                rejected = self.client.post("/tubes/save", data=self.payload(id=str(item_id), code=code, weight_mode="auto"))
                self.assertEqual(rejected.status_code, 400)
                body = rejected.get_data(as_text=True)
                self.assertIn("KG6028", body)
                self.assertIn("手工", body)
                self.assertEqual(self.tubes.get_item(item_id), before)

        page = self.client.get(f"/tubes/{item_id}/edit")
        self.assertEqual(page.status_code, 200)
        fields = FormFields(page.get_data(as_text=True))
        self.assertEqual(fields.selected("weight_mode"), ["manual"])
        self.assertEqual(fields.field("weight_kg")["value"], precision)
        self.assertNotIn("readonly", fields.field("weight_kg"))
        saved = self.client.post("/tubes/save", data=self.payload(
            id=str(item_id), code="KG6028", weight_mode="manual", weight_kg=precision,
            weight_length_mm="50", note="保持手工原重量"))
        self.assertEqual(saved.status_code, 302)
        current = self.tubes.get_item(item_id)
        self.assertEqual((current["weight_mode"], current["weight_kg"], current["weight_length_mm"]),
                         ("manual", float(precision), 50))
        self.assertEqual(Decimal(self.lookup("KG6028")[0]["gross_weight"]), Decimal(precision))

    def test_other_kg_code_keeps_general_automatic_calculation(self):
        response = self.client.post("/tubes/save", data=self.payload(code="KG8097"))
        self.assertEqual(response.status_code, 302)
        record, = self.records("KG8097")
        self.assertEqual(record["weight_mode"], "auto")
        self.assertAlmostEqual(record["weight_kg"], 0.1353899503125, places=14)

    def test_auto_save_recomputes_forged_weight_and_changed_length(self):
        response = self.client.post("/tubes/save", data=self.payload())
        self.assertEqual(response.status_code, 302)
        record, = self.records()
        self.assertEqual(record["weight_mode"], "auto")
        self.assertEqual(record["weight_length_mm"], 41.5)
        self.assertAlmostEqual(record["weight_kg"], 0.1353899503125, places=14)
        self.assertEqual(record["purchase_base"], 8)
        changed = self.client.post("/tubes/save", data=self.payload(id=str(record["id"]),
                                   weight_length_mm="50", weight_kg="888"))
        self.assertEqual(changed.status_code, 302)
        updated = self.tubes.get_item(record["id"])
        self.assertEqual(updated["weight_length_mm"], 50)
        self.assertAlmostEqual(updated["weight_kg"], 0.1611064771875, places=14)

    def test_missing_length_saves_unknown_weight_and_does_not_infer_specification(self):
        response = self.client.post("/tubes/save", data=self.payload(weight_length_mm="", blank_length_text="41.5"))
        self.assertEqual(response.status_code, 302)
        record, = self.records()
        self.assertEqual(record["weight_mode"], "auto")
        self.assertIsNone(record["weight_length_mm"])
        self.assertIsNone(record["weight_kg"])
        self.assertEqual(self.lookup(self.code), [])

    def test_invalid_new_input_returns_400_and_preserves_values_and_new_title(self):
        data = self.payload(weight_length_mm="不是数字", active="0", note="保留备注 & 尺寸", weight_kg="123")
        response = self.client.post("/tubes/save", data=data)
        self.assertEqual(response.status_code, 400)
        body = response.get_data(as_text=True)
        fields = FormFields(body)
        self.assertIn("新增管件", body)
        self.assertNotIn("编辑管件", body)
        self.assertIn('role="alert"', body)
        self.assertIn("计重长度必须是有限数字", body)
        for name in ("code", "spec_text", "outer_diameter_mm", "inner_diameter_mm", "weight_length_mm", "weight_kg"):
            self.assertEqual(fields.field(name)["value"], data[name])
        self.assertEqual(fields.selected("weight_mode"), ["auto"])
        self.assertNotIn("checked", fields.field("active"))
        self.assertIn("保留备注 &amp; 尺寸", body)
        self.assertEqual(self.records(), [])

    def test_invalid_edit_preserves_submission_and_existing_record(self):
        item_id = self.tubes.save(self.payload(), actor="fixture")
        before = self.tubes.get_item(item_id)
        data = self.payload(id=str(item_id), inner_diameter_mm="42", weight_length_mm="65", note="修改未丢失")
        response = self.client.post("/tubes/save", data=data)
        self.assertEqual(response.status_code, 400)
        body = response.get_data(as_text=True)
        fields = FormFields(body)
        self.assertIn("编辑管件", body)
        self.assertNotIn("新增管件", body)
        self.assertIn("外径必须大于内径", body)
        for name in ("id", "inner_diameter_mm", "weight_length_mm"):
            self.assertEqual(fields.field(name)["value"], data[name])
        self.assertIn("修改未丢失", body)
        self.assertEqual(self.tubes.get_item(item_id), before)

    def test_auto_calculation_routes_still_require_login_and_tube_permission(self):
        from app.modules.admin.factory import get_admin_service

        anonymous = self.web.app.test_client()
        username = "tube-viewer-" + uuid4().hex[:8]
        with self.client.session_transaction() as session:
            actor_id = session["user_id"]
        get_admin_service().save_user({"username": username, "display_name": username,
                                      "password": "viewer-password", "role": "viewer", "active": "1",
                                      "permission_overrides": {"manage_tube_items": "deny"}},
                                     actor="007", actor_user_id=actor_id)
        viewer = self.web.app.test_client()
        self.assertEqual(viewer.post("/login", data={"username": username, "password": "viewer-password"}).status_code, 302)
        for client, status in ((anonymous, 401), (viewer, 403)):
            with self.subTest(status=status):
                headers = {"Accept": "application/json"}
                self.assertEqual(client.get("/tubes/new", headers=headers).status_code, status)
                self.assertEqual(client.post("/tubes/save", data=self.payload(), headers=headers).status_code, status)
        self.assertEqual(self.records(), [])

    def test_real_csrf_rejects_missing_or_invalid_token_before_saving(self):
        token = self.csrf()
        self.web.app.config["TESTING"] = False
        for submitted in ("", "invented-token"):
            with self.subTest(token=submitted):
                rejected = self.client.post("/tubes/save", data=self.payload(csrf_token=submitted),
                                            headers={"Accept": "application/json"})
                self.assertEqual(rejected.status_code, 400)
                self.assertEqual(self.records(), [])
        saved = self.client.post("/tubes/save", data=self.payload(csrf_token=token))
        self.assertEqual(saved.status_code, 302)
        record, = self.records()
        self.assertAlmostEqual(record["weight_kg"], 0.1353899503125, places=14)

    def test_borrowed_form_save_preserves_manual_snapshot_and_auto_stays_unknown(self):
        source_id = self.tubes.save(self.payload(), actor="fixture")
        for mode in ("manual", "auto"):
            alias = self.code + "-" + mode.upper()
            alias_id = self.tubes.save(self.payload(code=alias, borrowed_from=self.code,
                                       weight_mode=mode, weight_kg="0.123456789123"), actor="fixture")
            expected = 0.123456789123 if mode == "manual" else None
            for submitted in ("", "999"):
                with self.subTest(mode=mode, submitted=submitted):
                    response = self.client.post("/tubes/save", data=self.payload(
                        id=str(alias_id), code=alias, borrowed_from=self.code, weight_mode=mode,
                        weight_kg=submitted, note="只更新备注"))
                    self.assertEqual(response.status_code, 302)
                    saved = self.tubes.get_item(alias_id)
                    self.assertEqual(saved["weight_kg"], expected)
                    self.assertEqual(saved["note"], "只更新备注")
                    self.assertEqual(Decimal(self.lookup(alias)[0]["gross_weight"]), Decimal("0.1353899503125"))

        source = self.tubes.get_item(source_id)
        self.tubes.save({**source, "borrowed_codes": ""}, actor="fixture")
        detached = self.lookup(self.code + "-MANUAL")
        self.assertEqual(Decimal(detached[0]["gross_weight"]), Decimal("0.123456789123"))

    def test_borrowed_cost_lookup_refreshes_source_but_keeps_saved_cost_snapshot(self):
        alias = self.code + "-ALIAS"
        source_id = self.tubes.save(self.payload(borrowed_codes=alias), actor="fixture")
        candidate, = self.lookup(alias)
        self.assertEqual((candidate["source_id"], candidate["product_code"]), (self.code, alias))
        self.assertEqual(Decimal(candidate["gross_weight"]), Decimal("0.1353899503125"))
        draft = {"code": self.code + "-COST", "kind": "arm", "name": "管件成本快照",
                 "items": [{"name": "焊接管", "product_code": alias, "mode": "weight", "unit_price": "6",
                            "weight_choice": candidate["token"]}]}
        headers = {"Accept": "application/json", "X-CSRF-Token": self.csrf()}
        created = self.client.post("/costs/new", json=draft, headers=headers)
        self.assertEqual(created.status_code, 201, created.get_json())
        record = created.get_json()["record"]
        old_weight = record["items"][0]["weight"]
        self.assertEqual(Decimal(old_weight), Decimal("0.1353899503125"))

        changed = self.client.post("/tubes/save", data=self.payload(id=str(source_id), weight_length_mm="50"))
        self.assertEqual(changed.status_code, 302)
        latest, = self.lookup(alias)
        self.assertEqual(Decimal(latest["gross_weight"]), Decimal("0.1611064771875"))
        self.assertEqual(self.client.post("/costs/preview", json=draft, headers=headers).status_code, 409)
        retained = self.client.post(created.get_json()["url"], json={"version": record["version"], "remark": "来源已改长"},
                                    headers=headers)
        self.assertEqual(retained.status_code, 200, retained.get_json())
        self.assertEqual(retained.get_json()["record"]["items"][0]["weight"], old_weight)

        # Clearing a required source dimension must not resurrect the alias's old stored weight.
        missing = self.client.post("/tubes/save", data=self.payload(id=str(source_id), weight_length_mm=""))
        self.assertEqual(missing.status_code, 302)
        self.assertEqual(self.lookup(alias), [])
