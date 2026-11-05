from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.modules.costing.historical_cost_amounts import plan_historical_costs
from app.modules.costing.pair_catalog import CostingPairCatalog
from app.modules.costing.repository import initialize_costing_database
from app.modules.costing.service import CostingService
from app.modules.costing.transfer_service import CostingTransferService


class HistoricalAmountsTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "cost.sqlite3"
        initialize_costing_database(self.path)
        self.service = CostingService(self.path)
        self.material = {"id": 1, "code": "A10001", "name": "主板", "model": "1000", "spec": "合成规格",
                         "sync_id": "material-1", "pieces": "4", "gross_weight": "2", "net_weight": "1.2",
                         "scrap_weight": "0.8"}
        self.matches = {("1000", "主板"): self.material}
        self.raw = {"code": "K1000LA", "approved_cost": "59", "suggested_tax_price_override": "80",
                    "scrap_credit": "4.38", "theoretical_scrap": "4.38", "items": [
                        {"name": "主板", "mode": "weight", "quantity": "2", "weight": "1.999", "unit_price": "3",
                         "source_info": {"raw": {"A": "K1000LA", "E": "主板", "K": "1.999"}}}],
                    "source_info": {"pivot": {"raw": {"A": "K1000LA", "F": "4.38", "K": "=SUMIF(...)"},
                                              "cached": {"K": "4.38"}},
                                    "scrap_reference_rows": [{"code": "1000", "name": "主板", "net_weight": "1.2"}]}}

    def add(self, **extra):
        return self.service.import_records([dict(self.raw, **extra)], actor="import")["records"][0]

    def saved(self, row, **fields):
        return self.service.update(row["id"], fields, actor="人工", expected_version=row["version"])

    def plan(self):
        # Service().list appends display-only fields to paired records; use exported stored snapshots.
        s = CostingTransferService(self.path).export_snapshot()
        return plan_historical_costs(s["records"], self.matches, lambda key: [r for r in s["revisions"] if r["record_id"] == key])

    def test_complete_recalculation_retains_manual_approved_cost_price_and_sources(self):
        original = self.add()
        changes, reviews = self.plan()
        self.assertEqual(reviews, [])
        before, after = changes[0]
        self.assertEqual(after["source_info"], original["source_info"])
        self.assertEqual((after["approved_cost"], after["suggested_tax_price_override"]), ("59", "80"))
        self.assertEqual((after["items"][0]["weight"], after["items"][0]["product_code"]), ("2", "A10001"))
        self.assertEqual((after["theoretical_scrap"], after["scrap_credit"], after["net_cost"]), ("3.2", "2.2", "56.8"))
        self.assertIsNone(after["scrap_credit_override"])
        self.assertEqual(before["scrap_credit"], "4.38")
        self.saved(original, **{k: after[k] for k in ("items", "scrap_mode", "scrap_unit_price", "scrap_credit_override")})
        self.assertEqual(self.plan(), ([], []))

    def test_manual_zero_and_later_restore_to_original_figure(self):
        r = self.saved(self.add(), scrap_credit="0")
        changes, reasons = self.plan()
        self.assertFalse(reasons)
        self.assertEqual(changes[0][1]["scrap_credit_override"], "0")
        self.saved(r, scrap_credit="4.38")
        self.assertEqual(self.plan()[0][0][1]["scrap_credit_override"], "4.38")

    def test_protect_manually_changed_and_cleared_theory_or_credit(self):
        row = self.saved(self.add(), scrap_credit=None)
        self.assertIn("被清空", self.plan()[1][0]["reason"])
        self.saved(row, theoretical_scrap="5")
        self.assertIn("理论参考额", self.plan()[1][0]["reason"])

    def test_auto_override_zero_stays_zero(self):
        row = self.add()
        proposed = self.plan()[0][0][1]
        self.saved(row, items=proposed["items"], scrap_mode="auto", scrap_credit_override="0")
        self.assertEqual(self.plan(), ([], []))

    def test_manual_weight_or_source_conflict_skips_sheet(self):
        row = self.add()
        self.saved(row, items=[{**row["items"][0], "weight": "2.3"}])
        changes, reasons = self.plan()
        self.assertFalse(changes)
        self.assertIn("手工修改", reasons[0]["reason"])

    def test_missing_material_duplicate_names_fixed_rows_never_count_partial_sum(self):
        self.raw["source_info"]["scrap_reference_rows"].append({"code": "1000", "name": "加强板", "net_weight": "1"})
        self.add(items=[*self.raw["items"], {"name": "加强板", "mode": "fixed", "amount": "2"}])
        self.assertIn("缺少唯一", self.plan()[1][0]["reason"])
        self.matches[("1000", "加强板")] = {**self.material, "id": 2, "code": "A10002", "sync_id": "material-2",
                                           "name": "加强板", "net_weight": "1", "scrap_weight": "1"}
        self.assertIn("未按重量计价", self.plan()[1][0]["reason"])

    def test_duplicate_rows_block_even_when_their_weights_match(self):
        first = self.raw["items"][0]
        self.add(items=[first, deepcopy(first)])
        self.assertIn("无法唯一", self.plan()[1][0]["reason"])

    def test_duplicate_name_requires_verified_codes_and_independent_remaining_source(self):
        source = {"kind": "stamping", "product_code": "A10001", "source_id": "material-1",
                  **{k: self.material[k] for k in ("name", "model", "spec", "pieces", "gross_weight", "net_weight", "scrap_weight")}}
        first = dict(self.raw["items"][0], product_code="A10001", weight_source=source)
        second = dict(self.raw["items"][0], product_code="A10002")
        row = self.add(items=[first, second])
        self.assertIn("另一同名行", self.plan()[1][0]["reason"])
        self.saved(row, items=[row["items"][0], dict(row["items"][1], weight_source={
            **source, "product_code": "A10002", "source_id": "other-verified-material"})])
        changes, reasons = self.plan()
        self.assertFalse(reasons)
        self.assertEqual([x["weight_source"]["source_id"] for x in changes[0][1]["items"]],
                         ["material-1", "other-verified-material"])

    def test_recalculation_cannot_replace_unrelated_historical_line_cost(self):
        self.add(items=[*self.raw["items"], {"name": "旧工费", "amount": "3"}])
        source = CostingTransferService(self.path).export_snapshot()
        # An older pricing rule adopted 2.99 while preserving the original amount cell.
        source["records"][0]["items"][1]["line_total"] = "2.99"
        source["revisions"][0]["after"] = deepcopy(source["records"][0])
        changes, reasons = plan_historical_costs(source["records"], self.matches, source["revisions"])
        self.assertFalse(changes)
        self.assertIn("旧工费 未参与本次补录", reasons[0]["reason"])

    def test_repeating_carton_division_requires_exact_fifty_digit_reproduction(self):
        self.add(items=[*self.raw["items"], {"name": "纸箱", "mode": "package", "unit_price": "1",
                                            "quantity": "3", "strap_rate": "0"}])
        changes, reasons = self.plan()
        self.assertFalse(reasons)
        before, after = changes[0]
        self.assertEqual(before["items"][1]["line_total"], "0." + "3" * 50)
        self.assertEqual(after["items"][1]["line_total"], "0." + "3" * 100)
        exported = CostingTransferService(self.path).export_snapshot()
        row = exported["records"][0]["items"][1]
        with localcontext() as c:
            c.prec = 100
            stored = Decimal(row["line_total"])
            for deviation in ("0.01", "1E-50", "1E-60"):
                row["line_total"] = str(stored + Decimal(deviation))
                exported["revisions"][0]["after"] = deepcopy(exported["records"][0])
                proposals, reviews = plan_historical_costs(exported["records"], self.matches, exported["revisions"])
                self.assertFalse(proposals)
                self.assertIn("重算将改变其历史金额", reviews[0]["reason"])

    def test_confirmed_shared_pair_and_conflicting_shared_code(self):
        original = self.add()
        catalog = CostingPairCatalog(self.service)
        catalog.mirror_selected([{"id": original["id"], "version": original["version"]}], actor="配对")
        changes, reasons = self.plan()
        self.assertEqual(reasons, [])
        self.assertEqual({a["code"] for _, a in changes}, {"K1000LA", "K1000RA"})
        for before, after in changes:
            self.assertEqual(after["items"][0]["product_code"], "A10001")
            self.assertEqual(after["items"][0]["pair_kind"], "shared")
            self.assertEqual(before["pair"], after["pair"])
        self.matches[("1000", "主板")] = {**self.material, "code": "A1000L"}
        changes, reasons = self.plan()
        self.assertFalse(changes)
        self.assertEqual(len(reasons), 2)
