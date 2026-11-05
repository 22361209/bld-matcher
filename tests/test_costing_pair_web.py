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
