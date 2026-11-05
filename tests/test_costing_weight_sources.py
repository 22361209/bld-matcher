from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.modules.costing.business_sync import validate_costing
from app.modules.costing.domain import CostingValidationError, calculate_cost
from app.modules.costing.grid import EMPTY_FILTER_VALUE
from app.modules.costing.repository import initialize_costing_database
from app.modules.costing.service import CostingService
from app.modules.costing.transfer_service import CostingTransferService, snapshot_digest


def stamp(**fields):
    return {"kind": "stamping", "product_code": "A1000", "source_id": "material-q1", "name": "主板",
            "model": "测试", "spec": "测试规格", "gross_weight": "2.19", "net_weight": "1.19",
            "scrap_weight": "1", "pieces": "3", **fields}


def sheet(**fields):
    return {"code": "QA1000LA", "scrap_mode": "auto", "items": [
        {"name": "主板", "product_code": "A1000", "mode": "weight", "unit_price": "10", "quantity": "2",
         "weight_source": stamp()},
    ], **fields}


class CostingWeightSourceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "costs.sqlite3"
        initialize_costing_database(self.path)
        self.service = CostingService(self.path)

    def update(self, r, **changes):
        return self.service.update(r["id"], {"peer_version": r.get("peer_version"), **changes},
                                   expected_version=r["version"], actor="qa")

    def test_stamping_quantity_rate_floor_and_precedence(self):
        r = self.service.create(sheet(), actor="qa")
        self.assertEqual((r["items"][0]["weight"], r["items"][0]["scrap_weight"], r["scrap_known_weight"],
                          r["scrap_unit_price"], r["theoretical_scrap"], r["scrap_credit"], r["net_cost"]),
                         ("2.19", "1", "2", "2", "4", "3", "40.8"))
        r = self.update(r, scrap_credit_override="0")
        self.assertEqual((r["scrap_credit"], r["net_cost"]), ("0", "43.8"))
        r = self.update(r, scrap_credit_override="", scrap_unit_price="0.2")
        self.assertEqual((r["scrap_credit_override"], r["theoretical_scrap"], r["scrap_credit"]), (None, "0.4", "0"))
        r = self.update(r, scrap_unit_price="3")
        self.assertEqual((r["scrap_credit"], r["net_cost"]), ("5", "38.8"))
        self.assertEqual(r, self.service.get(r["id"]))

    def test_partial_stamps_preserve_manual_actual_and_unrelated_rows(self):
        rows = sheet()["items"]
        rows.extend([
            {"name": "冲压件", "mode": "weight", "product_code": "B1000", "unit_price": "1",
             "weight_source": stamp(product_code="B1000", net_weight=None, scrap_weight=None)},
            {"name": "副板", "mode": "weight", "weight": ".6", "unit_price": "1"},
            {"name": "加强板", "product_code": "X", "mode": "weight", "weight": ".1", "unit_price": "1"},
            {"name": "焊接管", "mode": "weight", "weight": ".2", "unit_price": "1"},
            {"name": "电泳", "mode": "weight", "weight": ".2", "unit_price": "1"},
        ])
        r = calculate_cost(sheet(items=rows, scrap_credit="9", theoretical_scrap="99"))
        self.assertEqual((r["scrap_known_amount"], r["scrap_complete"], r["scrap_credit"], r["net_cost"]),
                         ("4", False, "0", "47.09"))
        self.assertIsNone(r["theoretical_scrap"])
        self.assertEqual(r["minimum_tax_price"], "64.75")
        self.assertEqual([x["scrap_expected"] for x in r["items"]], [True, True, True, True, False, False])
        self.assertIn("缺净重", r["items"][1]["scrap_issue"])
        self.assertIn("缺产品编码", r["items"][2]["scrap_issue"])
        self.assertIn("来源待确认", r["items"][3]["scrap_issue"])
        actual = calculate_cost(sheet(items=rows, approved_cost="50", scrap_credit_override="2"))
        self.assertEqual((actual["scrap_credit"], actual["net_cost"], actual["status"]), ("2", "48", "draft"))
        self.assertEqual(calculate_cost(sheet(items=[rows[-1]]))["scrap_credit"], "0")

    def test_missing_scrap_uses_manual_override_or_zero_without_inventing_missing_cost(self):
        rows = [{"name": "主板", "mode": "weight", "unit_price": "5", "weight": "4"},
                {"name": "加工费", "mode": "fixed"}]
        r = self.service.create(sheet(items=rows), actor="qa")
        self.assertEqual((r["scrap_credit"], r["known_subtotal"], r["calculated_cost"], r["net_cost"],
                          r["minimum_tax_price"], r["status"]), ("0", "20", None, None, None, "draft"))
        for override, credit, net, price in [("4", "4", "16", "22"), ("0", "0", "20", "27.5"),
                                            (None, "0", "20", "27.5")]:
            r = self.update(r, approved_cost="20", scrap_credit_override=override)
            self.assertEqual((r["scrap_credit_override"], r["scrap_credit"], r["net_cost"], r["minimum_tax_price"]),
                             (override, credit, net, price))
            self.assertIsNone(r["calculated_cost"])
            self.assertFalse(r["scrap_complete"])
            self.assertEqual(r["status"], "draft")

    def test_legacy_blank_deduction_survives_get_edit_roundtrip(self):
        record = self.service.create({"code": "LEGACY", "items": [{"name": "零件", "amount": "10"}]}, actor="qa")
        before = self.service.get(record["id"])
        self.assertIsNone(before["scrap_credit"])
        after = self.update(before, **{**before, "remark": "已复核"})
        self.assertEqual((after["scrap_credit"], after["net_cost"]), (None, "10"))
        self.assertIsNone(self.service.revisions(record["id"])[0]["after"]["scrap_credit"])

    def test_older_pending_snapshot_is_readable_filterable_and_preserved_across_sync(self):
        rows = [{"name": "主板", "mode": "weight", "weight": "2", "unit_price": "9.916696"}]
        record = self.service.create(sheet(code="K6001B", approved_cost="20", items=rows), actor="qa")
        snapshot = CostingTransferService(self.path).export_snapshot()
        for version in (snapshot["records"][0], snapshot["revisions"][0]["after"]):
            for key in ("scrap_credit", "net_cost", "minimum_tax_price", "suggested_tax_price"):
                version[key] = None
        snapshot["digest"] = snapshot_digest(snapshot)
        target = self.path.with_name("old.sqlite3")
        initialize_costing_database(target)
        receiver = CostingTransferService(target)
        receiver.apply(snapshot, actor="qa", expected_digest=snapshot["digest"])
        installed = CostingService(target)
        reopened = installed.get(record["id"])
        self.assertEqual((reopened["calculated_cost"], reopened["scrap_credit"], reopened["net_cost"],
                          reopened["minimum_tax_price"], reopened["suggested_tax_price"], reopened["status"]),
                         ("19.833392", "0", "20", "27.5", "27.5", "draft"))
        self.assertFalse(reopened["scrap_complete"])
        self.assertIsNone(reopened["theoretical_scrap"])
        filters = {"scrap": "0", "net": "20", "minimum_price": "27.5", "status": "draft"}
        self.assertEqual(installed.list(q="K6001B", column_filters=filters)["records"], [reopened])
        options = installed.filter_options(q="K6001B")
        for field, value in filters.items():
            self.assertEqual([row["value"] for row in options[field]], [value])
        self.assertEqual(installed.list(column_filters={"net": EMPTY_FILTER_VALUE})["total"], 0)
        self.assertEqual(receiver.export_snapshot(), snapshot)
        updated = installed.update(record["id"], {**reopened, "remark": "已复核"},
                                   expected_version=1, actor="qa")
        self.assertEqual(updated["items"], record["items"])
        self.assertEqual(updated["net_cost"], "20")
        self.assertEqual(installed.revisions(record["id"])[0]["before"], snapshot["records"][0])
        current = receiver.export_snapshot()
        self.assertEqual(validate_costing(current)["records"], current["records"])

        for field, value in (("scrap_credit", "1"), ("net_cost", "99")):
            broken = deepcopy(snapshot)
            broken["records"][0][field] = broken["revisions"][0]["after"][field] = value
            broken["digest"] = snapshot_digest(broken)
            with self.subTest(field=field), self.assertRaisesRegex(CostingValidationError, "核算快照"):
                validate_costing(broken)

    def test_detach_on_weight_change_and_retain_locked_history_for_unrelated_edits(self):
        raw = sheet()["items"][0]
        raw.pop("weight_source")
        raw["weight"] = "2.19"
        old = self.service.create({"code": "QA-old", "items": [raw], "scrap_credit": "4.38",
                                   "theoretical_scrap": "4.38"}, actor="qa", source="excel")
        r = self.update(old, remark="文字")
        self.assertNotIn("scrap_mode", r)
        self.assertEqual((r["scrap_credit"], r["net_cost"]), (old["scrap_credit"], old["net_cost"]))
        r = self.update(r, scrap_mode="auto", items=[{**r["items"][0], "weight_source": stamp()}])
        self.assertEqual((r["theoretical_scrap"], r["scrap_credit"]), ("4", "3"))
        old_client_row = {k: v for k, v in r["items"][0].items() if k != "weight_source"}
        r = self.update(r, items=[{**old_client_row, "remark": "新备注"}])
        self.assertIsNotNone(r["items"][0]["weight_source"])
        r = self.update(r, items=[{**old_client_row, "weight": "2.25"}])
        self.assertIsNone(r["items"][0]["weight_source"])
        self.assertEqual(r["scrap_credit"], "0")
        self.assertEqual(r["items"][0]["weight"], "2.25")
        self.assertEqual(self.service.revisions(r["id"])[-1]["after"]["scrap_credit"], "4.38")

    def test_tube_does_not_contribute_and_invalid_source_is_rejected(self):
        tube = stamp(kind="tube", pieces=None, net_weight=None, scrap_weight=None)
        r = calculate_cost(sheet(items=[{"name": "焊接管", "product_code": "A1000", "mode": "weight",
                                       "unit_price": "3", "weight_source": tube}]))
        self.assertEqual((r["scrap_complete"], r["scrap_known_weight"], r["net_cost"]), (True, "0", "6.57"))
        for wrong in [stamp(gross_weight="-1"), stamp(net_weight="3"), stamp(scrap_weight="0.1"),
                      stamp(product_code="wrong"), stamp(kind="other"), tube | {"net_weight": "1"}]:
            with self.subTest(wrong=wrong), self.assertRaises(CostingValidationError):
                calculate_cost(sheet(items=[dict(sheet()["items"][0], weight_source=wrong)]))

    def test_pair_usage_is_independent_and_changed_shared_code_invalidates_both(self):
        a = self.service.create(sheet(create_pair=True), actor="qa")
        b = self.service.get(a["pair"]["peer_id"])
        b = self.update(b, items=[dict(b["items"][0], quantity="3")])
        self.assertEqual((b["theoretical_scrap"], b["scrap_credit"]), ("6", "5"))
        a = self.service.get(a["id"])
        self.assertEqual((a["theoretical_scrap"], a["scrap_credit"]), ("4", "3"))
        a = self.update(a, items=[dict(a["items"][0], product_code="B1000")])
        b = self.service.get(b["id"])
        for current in (a, b):
            self.assertIsNone(current["items"][0]["weight_source"])
            self.assertTrue(current["items"][0]["scrap_expected"])
            self.assertEqual(current["scrap_credit"], "0")
        self.assertEqual((a["items"][0]["quantity"], b["items"][0]["quantity"]), ("2", "3"))

    def test_sync_preserves_sources_and_rejects_inconsistent_new_financial_fields(self):
        r = self.service.create(sheet(create_pair=True), actor="qa")
        self.update(r, scrap_credit_override="0")
        snapshot = CostingTransferService(self.path).export_snapshot()
        self.assertEqual(snapshot["format_version"], 3)
        self.assertEqual(validate_costing(snapshot)["records"], snapshot["records"])
        target = self.path.with_name("target.sqlite3")
        initialize_costing_database(target)
        receiver = CostingTransferService(target)
        receiver.apply(snapshot, actor="qa", expected_digest=snapshot["digest"])
        self.assertEqual(receiver.export_snapshot(), snapshot)
        altered = deepcopy(snapshot)
        record = altered["records"][0]
        record["scrap_known_weight"] = "0.01"
        current = next(rev for rev in altered["revisions"]
                       if rev["record_id"] == record["id"] and rev["version"] == record["version"])
        current["after"] = record
        altered["digest"] = snapshot_digest(altered)
        with self.assertRaisesRegex(CostingValidationError, "核算快照"):
            validate_costing(altered)

    def test_v3_required_when_only_a_historical_revision_carries_a_tube_source(self):
        tube = stamp(kind="tube", pieces=None, net_weight=None, scrap_weight=None)
        r = self.service.create({"code": "old-tube", "scrap_credit": "4", "items": [
            {"name": "焊接管", "product_code": "A1000", "mode": "weight", "unit_price": "1",
             "weight_source": tube}]}, actor="qa")
        self.assertEqual(r["scrap_credit"], "4")
        self.update(r, items=[{"name": "人工", "amount": "3"}])
        snapshot = CostingTransferService(self.path).export_snapshot()
        self.assertEqual(snapshot["format_version"], 3)
        snapshot["format_version"] = 1
        snapshot["digest"] = snapshot_digest(snapshot)
        with self.assertRaisesRegex(CostingValidationError, "迁移包版本"):
            CostingTransferService(self.path).preview(snapshot)

    def test_high_precision_theory_is_derived_and_survives_text_edit_and_exchange(self):
        source = stamp(gross_weight="2.000000000000000001", net_weight="1", scrap_weight="1.000000000000000001")
        row = dict(sheet()["items"][0], weight_source=source, quantity="1.000000000000000001")
        r = self.service.create(sheet(items=[row], scrap_unit_price="2.000000000000000001"), actor="qa")
        self.assertGreater(len(r["theoretical_scrap"].split(".")[1]), 18)
        self.assertEqual(r["scrap_known_weight"], "1.000000000000000002000000000000000001")
        result = self.update(r, remark="已核对")
        self.assertEqual(r["net_cost"], result["net_cost"])
        self.assertEqual(CostingTransferService(self.path).export_snapshot()["records"][0]["scrap_credit"],
                         r["scrap_credit"])
