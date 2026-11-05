from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.modules.costing.domain import CostingConflict, CostingValidationError
from app.modules.costing.pair_catalog import CostingPairCatalog
from app.modules.costing.pair_codes import mirror_code
from app.modules.costing.repository import initialize_costing_database
from app.modules.costing.service import CostingService


class CostingPairCatalogTest(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "fixture.db"
        initialize_costing_database(path)
        self.service = CostingService(path)
        self.catalog = CostingPairCatalog(self.service)

    def create(self, code, items=()):
        return self.service.create({"code": code, "items": list(items)}, actor="input")

    def test_model_suffix_and_infix_are_reversible(self):
        for left, right in (("K8057LB", "K8057RB"), ("K8299LA01-618", "K8299RA01-618"),
                            ("K1-LC-7", "K1-RC-7"), ("LA007", "RA007")):
            self.assertEqual(mirror_code(left), right)
            self.assertEqual(mirror_code(right), left)
        for unrelated in ("BALL", "K1LB2RA", "K8057R", "K1LAB", "ABC"):
            self.assertEqual(mirror_code(unrelated), "")

    def test_candidates_skip_unrelated_models_and_never_overwrite_an_existing_hand(self):
        left = self.create("K8299LA01-618")
        right = self.create("K8299RA01-618")
        single = self.create("K1002RC")
        self.create("A-TUBE")
        rows = self.catalog.candidates()
        self.assertEqual([(row["code"], row["other_code"], row["other_id"]) for row in rows],
                         [("K1002RC", "K1002LC", None), ("K8299LA01-618", right["code"], right["id"])])
        with self.assertRaises(CostingConflict):
            self.catalog.mirror_selected([single, left], actor="pair")
        self.assertEqual(self.service.list()["total"], 4)
        self.assertEqual(self.service.get(single["id"])["version"], 1)

    def test_links_use_stable_rows_and_preserve_both_historical_prices(self):
        common = {"name": "胶套", "product_code": "C8043", "amount": "2"}
        bolt = {"name": "螺栓", "amount": "1"}
        left = self.create("K301LA", (common, bolt, bolt))
        right = self.create("K301RA", (bolt, {**common, "amount": "3"}, {"name": "挡圈", "amount": "1"}))
        preview = self.catalog.existing(left["id"])
        left_id, right_id = left["items"][0]["id"], right["items"][1]["id"]
        self.assertEqual(preview["suggestions"], {left_id: {"mode": "shared", "other_id": right_id}})
        chosen = [{"id": left_id, "mode": "shared", "other_id": right_id}]
        with self.assertRaises(CostingConflict):
            self.catalog.link_existing(left["id"], version=1, other_version=2, matches=chosen, actor="pair")
        with self.assertRaises(CostingValidationError):
            self.catalog.link_existing(left["id"], version=1, other_version=1, matches=chosen * 2, actor="pair")
        self.catalog.link_existing(left["id"], version=1, other_version=1, matches=chosen, actor="pair")
        left_after, right_after = [self.service.get(row["id"]) for row in (left, right)]
        self.assertEqual(self.catalog.candidates(), [])
        self.assertEqual([i["pair_kind"] for i in left_after["items"]], ["shared", "local", "local"])
        self.assertEqual([i["pair_kind"] for i in right_after["items"]], ["local", "shared", "local"])
        self.assertEqual((left_after["items"][0]["amount"], right_after["items"][1]["amount"]), ("2", "3"))
        self.assertEqual(left_after["pair"]["peer_id"], right["id"])

    def test_handed_part_codes_are_suggested_by_direction_for_new_and_existing_peers(self):
        left = self.create("K1299LC", ({"name": "左支架", "product_code": "G8354L", "amount": "1"},))
        item_id = left["items"][0]["id"]
        mirror = self.catalog.existing(left["id"])["suggestions"][item_id]
        self.assertEqual((mirror["mode"], mirror["peer_product_code"]), ("handed", "G8354R"))
        right = self.create("K1299RC", ({"name": "右支架", "product_code": "G8354R", "amount": "2"},))
        existing = self.catalog.existing(left["id"])["suggestions"]
        self.assertEqual(existing, {item_id: {"mode": "handed", "other_id": right["items"][0]["id"]}})

    def test_custom_single_mirror_requires_complete_plan_and_versions(self):
        right = self.create("K301RB", ({"name": "共用件", "amount": "2"}, {"name": "独有件", "amount": "7"}))
        selection = {"id": right["id"], "version": 1}
        with self.assertRaises(CostingValidationError):
            self.catalog.mirror_selected([{**selection, "rows": []}], actor="pair")
        with self.assertRaises(CostingConflict):
            self.catalog.mirror_selected([{**selection, "version": 2}], actor="pair")
        rows = [{"id": right["items"][i]["id"], "pair_kind": kind} for i, kind in enumerate(("shared", "local"))]
        self.assertEqual(self.catalog.mirror_selected([{**selection, "rows": rows}], actor="pair"), 1)
        created = self.service.list(q="K301LB")["records"][0]
        self.assertEqual((len(created["items"]), created["calculated_cost"]), (1, "2"))

    def test_reopened_pair_uses_stored_links_and_can_reassign_from_either_hand(self):
        from app.modules.costing.transfer_service import CostingTransferService

        common = {"name": "无编号支架", "amount": "2"}
        left = self.create("K301LB", (common, common, {"name": "垫圈", "amount": "13"}))
        right = self.create("K301RB", ({**common, "amount": "7"}, {**common, "amount": "8"}))
        a, b, c = [row["id"] for row in left["items"]]
        x, y = [row["id"] for row in right["items"]]
        initial = self.catalog.link_existing(left["id"], version=1, other_version=1,
                                             matches=[{"id": a, "other_id": y, "mode": "handed"}], actor="初次配对")
        right_view = self.catalog.existing(right["id"])
        self.assertTrue(right_view["paired"])
        self.assertEqual(right_view["other"]["id"], left["id"])
        self.assertEqual(right_view["suggestions"], {x: {"other_id": "", "mode": "local"},
                                                     y: {"other_id": a, "mode": "handed"}})
        changed = self.catalog.revise_existing(right["id"], version=2, other_version=2, matches=[
            {"id": x, "other_id": b, "mode": "shared"}, {"id": y, "other_id": "", "mode": "local"}], actor="调整")
        left_view = self.catalog.existing(left["id"])
        self.assertEqual((left_view["record"]["pair"], changed["version"], left_view["record"]["version"]),
                         (initial["pair"], 3, 3))
        self.assertEqual(left_view["suggestions"], {a: {"other_id": "", "mode": "local"},
                                                   b: {"other_id": x, "mode": "shared"},
                                                   c: {"other_id": "", "mode": "local"}})
        for new, original in ((changed, right), (left_view["record"], left)):
            self.assertEqual(new["calculated_cost"], original["calculated_cost"])
            self.assertEqual([{k: v for k, v in item.items() if k not in {"pair_kind", "peer_item_id"}}
                              for item in new["items"]], original["items"])
            self.assertEqual(self.service.revisions(new["id"])[0]["actor"], "调整")
        self.assertEqual(changed["updated_at"], left_view["record"]["updated_at"])

        # An unchanged resubmission should leave both histories as they are.
        self.catalog.revise_existing(left["id"], version=3, other_version=3,
                                     matches=[{"id": key, **row} for key, row in left_view["suggestions"].items()],
                                     actor="核对")
        self.assertEqual(self.catalog.existing(right["id"])["record"]["version"], 3)
        self.assertEqual(len(self.service.revisions(left["id"])), 3)
        items = left_view["record"]["items"]
        items[1]["amount"] = "10"
        self.service.update(left["id"], {"items": items, "peer_version": 3}, expected_version=3, actor="改单价")
        reopened = self.service.get(right["id"])
        self.assertEqual([row["amount"] for row in reopened["items"]], ["10", "8"])
        snapshot = CostingTransferService(self.service.db_path).export_snapshot()
        self.assertEqual((snapshot["format_version"], len(snapshot["records"]), len(snapshot["revisions"])), (2, 2, 8))

    def test_revising_validates_complete_plan_peer_identity_versions_and_shared_codes(self):
        left = self.create("K400LA", ({"name": "左支架", "product_code": "AL", "amount": "4"},))
        right = self.create("K400RA", ({"name": "右支架", "product_code": "AR", "amount": "5"},))
        row = {"id": left["items"][0]["id"], "other_id": right["items"][0]["id"], "mode": "handed"}
        with self.assertRaises(CostingConflict):
            self.catalog.revise_existing(left["id"], version=1, other_version=1, matches=[row], actor="user")
        self.catalog.link_existing(left["id"], version=1, other_version=1, matches=[row], actor="user")
        left_before, right_before = [self.service.get(sheet["id"]) for sheet in (left, right)]
        with self.assertRaises(CostingConflict):
            self.catalog.link_existing(left["id"], version=2, other_version=2, matches=[], actor="user")
        for current, other in ((2, 1), (1, 2)):
            with self.assertRaises(CostingConflict):
                self.catalog.revise_existing(left["id"], version=current, other_version=other, matches=[row],
                                             actor="user")
        for invalid in ([], [row, row], [{**row, "mode": "shared"}], [{**row, "mode": "local"}],
                        [{**row, "other_id": "wrong"}], [{**row, "id": "wrong"}]):
            with self.subTest(invalid=invalid), self.assertRaises(CostingValidationError):
                self.catalog.revise_existing(left["id"], version=2, other_version=2, matches=invalid, actor="user")
        self.assertEqual(self.service.get(left["id"]), left_before)
        self.assertEqual(self.service.get(right["id"]), right_before)
