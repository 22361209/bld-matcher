from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.modules.costing import repository
from app.modules.costing.domain import CostingConflict, CostingValidationError
from app.modules.costing.service import CostingService


class CostPairEditsTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "cost.sqlite3"
        repository.initialize_costing_database(self.path)
        self.service = CostingService(self.path)
        self.a = self.service.create({
            "code": "K8057LB", "create_pair": True, "approved_cost": "10", "suggested_tax_price_override": "33",
            "items": [{"name": "胶套", "product_code": "C8043", "part_code": "001", "mode": "unit",
                       "unit_price": "5", "quantity": "1", "source_info": {"sheet": "原表"}},
                      {"name": "支架", "product_code": "K1024LB", "amount": "4"},
                      {"name": "螺母", "amount": "2", "pair_kind": "local"}],
        }, actor="甲")
        self.b = self.service.get(self.a["pair"]["peer_id"])

    def save(self, record, **data):
        return self.service.update(record["id"], {"peer_version": record["peer_version"], **data},
                                   expected_version=record["version"], actor="乙")

    def test_shared_part_codes_and_prices_flow_both_directions_quantity_and_approved_are_independent(self):
        a, b = self.a, self.b
        self.assertEqual(b["peer_code"], a["code"])
        self.assertTrue(b["peer_has_manual_price"])
        self.assertFalse(a["peer_has_manual_price"])
        self.assertEqual([row["pair_kind"] for row in a["items"]], ["shared", "handed", "local"])
        self.assertEqual(b["items"][1]["product_code"], "K1024RB")
        self.assertEqual(b["items"][0]["peer_item_id"], a["items"][0]["id"])
        self.assertIsNone(b["approved_cost"])
        items = b["items"]
        items[0].update(unit_price="8", quantity="2", product_code="C8043A", part_code="002")
        items[1].update(product_code="右支架2", amount="3.5")
        b2 = self.save(b, items=items, suggested_tax_price_override="30")
        a2 = self.service.get(a["id"])
        self.assertEqual(a2["items"][0]["quantity"], "1")
        self.assertEqual(a2["items"][0]["product_code"], "C8043A")
        self.assertEqual(a2["items"][0]["part_code"], "002")
        self.assertEqual(a2["items"][0]["source_info"], {"sheet": "原表"})
        self.assertEqual(a2["items"][1]["product_code"], "K1024LB")
        self.assertEqual((a2["calculated_cost"], b2["calculated_cost"]), ("13.5", "19.5"))
        self.assertEqual((a2["approved_cost"], a2["suggested_tax_price_override"]), ("10", "33"))
        self.assertEqual(b2["suggested_tax_price_override"], "30")
        self.assertEqual(a2["updated_at"], b2["updated_at"])
        a2["items"][0]["unit_price"] = "6"
        self.save(a2, items=a2["items"])
        self.assertEqual(self.service.get(b["id"])["items"][0]["unit_price"], "6")

    def test_rows_added_removed_detached_and_reordered_by_stable_id(self):
        a = self.a
        shared, handed, local = a["items"]
        rows = [handed, {"name": "运费", "amount": "1"}, local, {**shared, "pair_kind": "local"}]
        a = self.save(a, items=rows)
        b = self.service.get(a["pair"]["peer_id"])
        self.assertEqual([row["name"] for row in b["items"]], ["胶套", "支架", "运费"])
        self.assertEqual([row["pair_kind"] for row in b["items"]], ["local", "handed", "shared"])
        self.assertEqual(b["items"][1]["peer_item_id"], a["items"][0]["id"])
        rows = [a["items"][0], a["items"][3]]  # Remove shared freight and the left-only nut.
        a = self.save(a, items=rows)
        b = self.service.get(b["id"])
        self.assertEqual([row["name"] for row in b["items"]], ["胶套", "支架"])
        a["items"][1].update(pair_kind="shared", product_code="C0002")
        self.save(a, items=a["items"])
        b = self.service.get(b["id"])
        self.assertEqual([(row["product_code"], row["pair_kind"]) for row in b["items"] if row["name"] == "胶套"],
                         [("C8043", "local"), ("C0002", "shared")])

    def test_mirror_directional_names_and_leave_common_or_ambiguous_names(self):
        a = self.service.create({"code": "K3333RA", "name": "右控制臂", "create_pair": True, "items": [
            {"name": name, "amount": "1"} for name in ["右支架", "左/右共用", "左右通用", "K5555RA"]
        ]}, actor="甲")
        peer = self.service.get(a["pair"]["peer_id"])
        self.assertEqual(peer["name"], "左控制臂")
        self.assertEqual([(r["name"], r["pair_kind"]) for r in peer["items"]],
                         [("左支架", "handed"), ("左/右共用", "shared"), ("左右通用", "shared"), ("K5555LA", "handed")])

    def test_real_component_suffixes_are_handed_without_extending_finished_model_rule(self):
        from app.modules.costing.pair_codes import mirror_code, mirror_part_code

        left = self.service.create({"code": "K6025LB", "create_pair": True, "items": [
            {"name": "B6004L", "product_code": "B6004L", "part_code": "KA8288L", "amount": "3"},
            {"name": "托盘", "product_code": "FA8121LD", "amount": "4"},
            {"name": "包装", "product_code": "10L", "part_code": "BOLT027", "amount": "1"},
        ]}, actor="甲")
        right = self.service.get(left["pair"]["peer_id"])
        self.assertEqual([(row["pair_kind"], row["product_code"]) for row in right["items"]],
                         [("handed", "B6004R"), ("handed", "FA8121RD"), ("shared", "10L")])
        self.assertEqual((right["items"][0]["name"], right["items"][0]["part_code"]), ("B6004R", "KA8288R"))
        self.assertEqual(mirror_part_code("K8274RD"), "K8274LD")
        self.assertEqual(mirror_code("K8274LD"), "")
        self.assertEqual(mirror_part_code("6004L"), "")

    def test_relation_switch_codes_and_modes_using_peer_values(self):
        a = self.a
        a["items"][1].update(pair_kind="shared", product_code="NEW", part_code="C")
        a["items"][0].update(mode="weight", weight=".3", unit_price="3.4", processing_fee=".2")
        self.save(a, items=a["items"])
        b = self.service.get(a["pair"]["peer_id"])
        self.assertEqual((b["items"][1]["product_code"], b["items"][1]["part_code"]), ("NEW", "C"))
        self.assertEqual(b["items"][0]["unit_price"], "3.4")
        self.assertEqual(b["items"][0]["processing_fee"], "0.2")
        self.assertIsNone(b["items"][0]["weight"])
        self.assertIn("请填写重量", b["items"][0]["issues"])

    def test_shared_carton_spec_updates_physical_dimensions_but_each_side_keeps_its_pack_count(self):
        left = self.service.create({"code": "K8888LA", "create_pair": True, "items": [
            {"name": "纸箱", "product_code": "32*20*11", "mode": "package", "unit_price": "2", "quantity": "4",
             "strap_rate": "1", "length": "32", "width": "20", "height": "11"}
        ]}, actor="甲")
        right = self.service.get(left["pair"]["peer_id"])
        right["items"][0].update(quantity="2")
        self.save(right, items=right["items"])
        left = self.service.get(left["id"])
        left["items"][0].update(product_code="35*21*12", part_code="BOX02", unit_price="3",
                                length="35", width="21", height="12")
        self.save(left, items=left["items"])
        right = self.service.get(right["id"])
        row = right["items"][0]
        self.assertEqual([row[k] for k in ("length", "width", "height", "quantity", "line_total")],
                         ["35", "21", "12", "2", "3.66"])

    def test_conflict_duplicate_unsupported_and_revision_failure_roll_back(self):
        a, b = self.a, self.b
        for version in (True, None, 999):
            with self.assertRaises(CostingConflict):
                self.service.update(a["id"], {"remark": "修改", "peer_version": version}, expected_version=1, actor="丁")
        with self.assertRaises(CostingValidationError):
            self.save(a, code="K5555LA")
        with self.assertRaises(CostingValidationError):
            self.save(a, items=[a["items"][0], a["items"][0]])
        self.service.create({"code": "K1234RA"}, actor="甲")
        with self.assertRaises(CostingConflict):
            self.service.create({"code": "K1234LA", "create_pair": True}, actor="甲")
        with self.assertRaises(CostingValidationError):
            self.service.create({"code": "OTHER", "create_pair": True}, actor="甲")
        writer = repository.SQLiteCostingRepository.add_revision

        def fail_after_peer(repo, before, after, **kwargs):
            if after["id"] == a["id"]:
                raise RuntimeError("audit")
            writer(repo, before, after, **kwargs)

        with patch.object(repository.SQLiteCostingRepository, "add_revision", fail_after_peer), self.assertRaises(RuntimeError):
            self.save(a, items=[])
        self.assertEqual(self.service.get(a["id"]), a)
        self.assertEqual(self.service.get(b["id"]), b)
        self.assertEqual(self.service.list()["total"], 3)

    def test_two_editors_starting_from_opposite_sides(self):
        def edit(record):
            try:
                return self.save(record, items=[{**record["items"][0], "unit_price": "4"}])
            except CostingConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(edit, (self.a, self.b)))
        self.assertEqual(sum(value is not None for value in outcomes), 1)
        for record in (self.a, self.b):
            self.assertEqual(self.service.get(record["id"])["version"], 2)

    def test_history_cleanup_will_not_bypass_paired_revision_guard(self):
        legacy = self.service.create({"code": "K5555RC", "items": [{"category": "衬套", "name": "C9907", "amount": "3"}]},
                                     actor="导入", source="excel")
        with self.service._uow(True) as uow:
            self.service._mirror_in_uow(uow.repository, legacy, None, "甲")
            uow.commit()
        plan = self.service.preview_line_codes()
        self.assertEqual(plan["records"], 0)
        self.assertEqual({r["code"] for r in plan["reviews"]}, {"K5555RC", "K5555LC"})
        self.assertEqual(self.service.apply_line_codes(plan["digest"], actor="管理员"), plan)

    def test_historical_mirror_and_metadata_edit_preserve_prior_amounts(self):
        base = self.service.create({"code": "K5555LC", "items": [{"amount": "2", "name": "左支架", "product_code": "K1024LC"}]}, actor="导入")
        with self.service._uow(True) as uow:
            # The transfer contract permits historical derived amounts that differ from today's formula.
            historical = deepcopy(base)
            historical["items"][0]["line_total"] = "7"
            historical.update(known_subtotal="7", effective_cost="7", net_cost="7", calculated_cost="7")
            self.assertTrue(uow.repository.update(historical, expected_version=base["version"]))
            paired = self.service._mirror_in_uow(uow.repository, historical, None, "甲")
            uow.commit()
        peer = self.service.get(paired["pair"]["peer_id"])
        self.assertEqual(peer["items"][0]["line_total"], "7")
        self.assertEqual((peer["items"][0]["product_code"], peer["items"][0]["name"]), ("K1024RC", "右支架"))
        paired["items"][0]["category"] = "装配"
        self.save(paired, items=paired["items"])
        peer = self.service.get(peer["id"])
        self.assertEqual((peer["net_cost"], peer["items"][0]["line_total"]), ("7", "7"))
