from __future__ import annotations

import re
from uuid import uuid4

from tests.web_app_test_base import WebAppTestBase


class CostPairWebTest(WebAppTestBase):
    def setUp(self):
        self.web.app.config["TESTING"] = True
        self.client = self.web.app.test_client()
        self.login()
        self.service = self.web.app.extensions["costing_service"]
        self.code = "WEB-" + uuid4().hex[:9].upper() + "-7144LB"
        self.token = re.search(r'name="csrf_token" value="([^"]+)"',
                               self.client.get("/costs/new").get_data(as_text=True)).group(1)
        self.web.app.config["TESTING"] = False

    def tearDown(self):
        self.web.app.config["TESTING"] = True

    def post_json(self, path, data):
        return self.client.post(path, json=data, headers={"Accept": "application/json", "X-CSRF-Token": self.token})

    def post_form(self, path, data):
        return self.client.post(path, data={**data, "csrf_token": self.token})

    def create(self, **options):
        return self.post_json("/costs/new", {"code": self.code, "kind": "arm", "items": [
            {"name": "胶套", "product_code": "C8022", "part_code": "BUSH", "amount": "9"},
            {"name": "左支架", "product_code": "F1144LB", "amount": "2"},
        ], **options}).get_json()["record"]

    def test_pair_entry_preview_automatic_updates_and_conflict(self):
        self.assertIn("左右件配对", self.client.get("/costs").get_data(as_text=True))
        self.assertIn("同时新建左右件", self.client.get("/costs/new").get_data(as_text=True))
        left = self.create(create_pair=True, approved_cost="25")
        edit = "/costs/" + left["id"]
        right_edit = "/costs/" + left["pair"]["peer_id"]
        page = self.client.get(edit).get_data(as_text=True)
        self.assertIn(f'href="{right_edit}">对侧 ', page)
        self.assertIn("请分别核对两侧", page)
        self.assertNotIn('name="create_pair"', page)
        self.assertNotIn("配对 " + left["peer_code"], page)
        new_items = [{**row, "amount": "6", "product_code": "BUSH2" if i == 0 else "L2",
                      "part_code": "N" if i == 0 else "S"} for i, row in enumerate(left["items"])]
        data = {"version": left["version"], "peer_version": left["peer_version"], "items": new_items}
        response = self.post_json(edit, data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        right = self.client.get(right_edit, headers={"Accept": "application/json"}).get_json()["record"]
        self.assertEqual([(row["product_code"], row["amount"]) for row in right["items"]], [("BUSH2", "6"), ("F1144RB", "6")])
        self.assertEqual(right["items"][0]["part_code"], "N")
        fresh = response.get_json()["record"]
        self.assertEqual(self.post_json(edit, {**data, "version": fresh["version"]}).status_code, 409)

    def test_preview_batch_and_existing_manual_match(self):
        one = self.create()
        review_url = "/costs/pairs/" + one["id"]
        self.assertIn("建立对侧并配对", self.client.get(review_url).get_data(as_text=True))
        self.assertNotIn("pair", self.service.get(one["id"]))
        # Both records now exist. A stale batch selection must not overwrite the peer.
        peer = self.create(code=self.code[:-2] + "RB")
        selection = {"candidate": f"{one['id']}:{one['version']}"}
        self.assertIn("核对两单并配对", self.client.get("/costs/pairs?q=" + self.code).get_data(as_text=True))
        self.assertEqual(self.post_form("/costs/pairs", selection).status_code, 409)
        self.assertIn("的现有明细", self.client.get(review_url).get_data(as_text=True))
        item, other = one["items"][0], peer["items"][0]
        form = {"row": [row["id"] for row in one["items"]], "action": "link", "version": "1", "other_version": "1",
                f"target-{item['id']}": other["id"], f"kind-{item['id']}": "shared"}
        self.assertEqual(self.post_form(review_url, form).status_code, 302)
        paired = self.service.get(one["id"])
        self.assertEqual([row["pair_kind"] for row in paired["items"]], ["shared", "local"])
        self.assertEqual(self.client.post("/costs/pairs", data=selection, headers={"Accept": "application/json"}).status_code, 400)

    def test_per_row_mirror_and_checked_batch(self):
        record = self.create()
        other = self.create(code=self.code + "-44")
        review_url = "/costs/pairs/" + record["id"]
        first, second = record["items"]
        data = {"action": "mirror", "version": "1", "row": [first["id"], second["id"]],
                f"kind-{first['id']}": "local", f"kind-{second['id']}": "handed",
                f"product-{second['id']}": "OPP", f"part-{second['id']}": "OPP-PART"}
        self.assertEqual(self.post_form(review_url, data).status_code, 302)
        self.assertEqual(self.post_form("/costs/pairs", {"candidate": f"{other['id']}:1"}).status_code, 302)
        paired = self.service.get(record["id"])
        peer = self.service.get(paired["pair"]["peer_id"])
        self.assertEqual([(row["product_code"], row["part_code"]) for row in peer["items"]], [("OPP", "OPP-PART")])
        self.assertIn("pair", self.service.get(other["id"]))

    def test_directory_finds_either_hand_after_pairing_and_limits_batch_selection(self):
        left = self.create(create_pair=True)
        pending = self.create(code=self.code + "-01")
        code_fragment = self.code[:-2]

        def table(**filters):
            response = self.client.get("/costs/pairs", query_string={"q": code_fragment, **filters})
            self.assertEqual(response.status_code, 200)
            return response.get_data(as_text=True).split("<tbody>")[1].split("</tbody>")[0]

        both = table()
        self.assertIn(pending["code"], both)
        self.assertIn(f'href="/costs/pairs/{left["id"]}">查看配对关系</a>', both)
        self.assertNotIn(f'name="candidate" value="{left["id"]}', both)
        self.assertIn(f'name="candidate" value="{pending["id"]}', both)
        for code in (left["code"], left["peer_code"]):
            linked = table(status="paired", q=code)
            self.assertEqual(linked.count("<tr>"), 1)
            self.assertIn(left["code"], linked)
            self.assertIn(left["peer_code"], linked)
            self.assertNotIn(pending["code"], linked)
        self.assertNotIn(f'href="/costs/pairs/{left["id"]}"', table(status="pending"))

    def test_existing_pair_keeps_review_entry_on_both_sheets_and_saves_full_mapping(self):
        left = self.create(create_pair=True)
        right = self.service.get(left["pair"]["peer_id"])
        a, b = left["items"]
        one, two = right["items"]
        for sheet, peer in ((left, right), (right, left)):
            review = "/costs/pairs/" + sheet["id"]
            page = self.client.get("/costs/" + sheet["id"]).get_data(as_text=True)
            self.assertIn(f'href="{review}">左右件配对</a>', page)
            self.assertIn(f'href="/costs/{peer["id"]}">对侧 ', page)
            reopened = self.client.get(review)
            self.assertEqual(reopened.status_code, 200)
            body = reopened.get_data(as_text=True)
            self.assertIn("保存配对关系", body)
            self.assertNotIn("建立对侧并配对", body)
            for entry in sheet["items"]:
                self.assertRegex(body, rf'value="{entry["peer_item_id"]}"[^>]* selected>')
        review = "/costs/pairs/" + left["id"]
        form = {"action": "revise", "version": str(left["version"]), "other_version": str(right["version"]),
                "row": [a["id"], b["id"]], f"target-{a['id']}": "", f"target-{b['id']}": two["id"],
                f"kind-{b['id']}": "handed"}
        missing = {key: value for key, value in form.items() if key != f"target-{a['id']}"}
        self.assertEqual(self.post_form(review, missing).status_code, 422)
        self.assertEqual(self.service.get(left["id"])["version"], left["version"])
        saved = self.post_form(review, form)
        self.assertEqual((saved.status_code, saved.location), (302, review))
        reopened = self.client.get(review).get_data(as_text=True)
        self.assertIn(right["code"] + " 的独有明细", reopened)
        right_after = self.service.get(right["id"])
        self.assertEqual([(r["id"], r["pair_kind"]) for r in right_after["items"]],
                         [(one["id"], "local"), (two["id"], "handed")])
        self.assertEqual(self.post_form(review, form).status_code, 409)

    def test_cost_viewer_can_read_saved_pair_but_cannot_create_or_change_pairing(self):
        from app.modules.admin.factory import get_admin_service

        left = self.create(create_pair=True)
        pending = self.create(code=self.code + "-2")
        with self.client.session_transaction() as session:
            admin_id = session["user_id"]
        username = "pair-viewer-" + uuid4().hex[:8]
        get_admin_service().save_user(
            {"username": username, "display_name": username, "password": "review-view-only", "role": "viewer",
             "active": "1", "permission_overrides": {"view_costs": "allow", "view_cost_list_full": "allow", "view_cost_details": "allow", "manage_costs": "deny"}},
            actor="007", actor_user_id=admin_id,
        )
        client = self.web.app.test_client()
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").get_data(as_text=True)).group(1)
        login = client.post("/login", data={"username": username, "password": "review-view-only", "csrf_token": csrf})
        self.assertEqual(login.status_code, 302)
        review = "/costs/pairs/" + left["id"]
        page = client.get(review)
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("已配对 · 只读", body)
        self.assertIn("左右共用件", body)
        self.assertIn("左右对应件", body)
        self.assertNotIn("保存配对关系", body)
        token = re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)
        blocked = client.post(review, data={"csrf_token": token, "action": "revise"}, headers={"Accept": "application/json"})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(client.get("/costs/pairs/" + pending["id"]).status_code, 403)
