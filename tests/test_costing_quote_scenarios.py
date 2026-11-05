from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
from itertools import product
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.modules.costing.domain import calculate_cost, displayed_cost
from app.modules.costing.quote_roles import line_quote_role
from app.modules.costing.quote_scenarios import OPTIONS, Scope, scope_quote
from app.modules.costing.repository import SQLiteCostingRepository, initialize_costing_database
from app.modules.costing.service import CostingService
from app.modules.costing.values import CostingValidationError


def model(**overrides):
    return calculate_cost({
        "code": "COST-Q1", "kind": "arm", "approved_cost": "100", "suggested_tax_price_override": "180",
        "items": [
            {"category": "焊接组件", "name": "主板", "amount": "12.345"},
            {"category": "焊接组件", "name": "电泳", "amount": "2"},
            {"category": "衬套", "name": "内部胶套", "amount": "3"},
            {"category": "球头1", "name": "球座", "amount": "4"},
            {"category": "球头1", "name": "内袋", "amount": "0.5"},
            {"category": "包装", "name": "64×31×26", "amount": "1"},
            {"name": "运费", "mode": "freight", "unit_price": ".8", "weight": "2.5"},
        ],
        **overrides,
    })


def quote(sheet, **flags):
    return scope_quote(sheet, Scope(**flags), details=True)


class CostingQuoteScenariosTest(TestCase):
    def test_all_32_combinations_and_full_sheet_approved_price(self):
        sheet = model()
        before = deepcopy(sheet)
        for stage, bits in product(("finished", "weld"), product((False, True), repeat=4)):
            with self.subTest(stage=stage, bits=bits):
                scope = Scope(stage=stage, **dict(zip(OPTIONS, bits, strict=True)))
                data = scope_quote(sheet, scope, details=True)
                gross = Decimal("12.345") + (7 if stage == "finished" else 0)
                addends = (Decimal(2), Decimal(".5"), Decimal(1), Decimal(2))
                gross += sum(v for v, include in zip(addends, bits, strict=True) if include)
                if all(bits) and stage == "finished":
                    gross = Decimal(100)
                self.assertEqual(data["problem"], "")
                self.assertEqual(Decimal(data["net_cost"]), gross)
                self.assertEqual(Decimal(data["minimum_tax_price"]),
                                 (gross * Decimal("1.375")).quantize(Decimal(".01"), rounding=ROUND_CEILING))
                self.assertEqual({row["index"] for row in data["included_lines"]} & {3, 4}, {3, 4} if stage == "finished" else set())
        self.assertEqual(sheet, before)
        self.assertEqual(sheet["suggested_tax_price"], "180")

    def test_independent_packaging_explicit_zero_and_missing_evidence(self):
        sheet = model()
        sheet["items"][4].update(name="内外箱", category="外箱")
        sheet["items"].pop(5)
        self.assertIn("第5行", quote(sheet, outer=False)["problem"])
        self.assertEqual(quote(sheet)["minimum_tax_price"], "137.5")
        self.assertEqual(quote(sheet, inner=False, outer=False)["net_cost"], "23.345")
        sheet["items"][4].update(name="包装工费", category="包装")
        self.assertIn("内包装、外包装", quote(sheet)["problem"])
        self.assertIn("确认包装工费", quote(sheet, inner=False)["problem"])
        sheet = model()
        sheet["items"].append({"category": "包装", "name": "包装工费", "line_total": "2", "quote_role": "packing_shared"})
        self.assertEqual(quote(sheet, outer=False)["net_cost"], "25.845")
        self.assertEqual(quote(sheet, inner=False, outer=False)["net_cost"], "23.345")
        sheet["items"].pop(1)
        self.assertIn("电泳", quote(sheet)["problem"])
        self.assertTrue(quote(sheet, ecoat=False)["minimum_tax_price"])
        sheet["items"].append({"category": "表面处理", "name": "电泳", "line_total": "0"})
        self.assertEqual(quote(sheet)["minimum_tax_price"], "137.5")

    def test_ambiguous_rows_can_be_resolved_without_repricing_saved_lines(self):
        sheet = model()
        sheet["items"][1].update(name="内部工序", category="表面处理", line_total=None)
        self.assertIn("第2行", quote(sheet, stage="weld", ecoat=False)["problem"])
        sheet["items"][1]["quote_role"] = "ecoat"
        self.assertIn("金额", quote(sheet, stage="weld")["problem"])
        self.assertEqual(quote(sheet, stage="weld", ecoat=False, inner=False, outer=False, freight=False)["minimum_tax_price"], "16.98")
        self.assertEqual(line_quote_role({"category": "焊接组件", "name": "运费"}), "unknown")
        self.assertEqual(line_quote_role({"category": "运费", "name": "采购运费"}), "unknown")
        self.assertEqual(line_quote_role({"category": "焊接组件", "name": "球头支架"}), "weld")

    def test_combined_option_costs_can_be_kept_or_removed_but_not_split_by_category(self):
        for name, category, first, second in (
            ("内外包装合计", "外包装", "inner", "outer"),
            ("内盒＋外箱", "内包装", "inner", "outer"),
            ("外箱含运费", "外包装", "outer", "freight"),
            ("电泳（含运费）", "表面处理", "ecoat", "freight"),
        ):
            with self.subTest(name=name):
                sheet = model(approved_cost=None, items=[{"category": "冲压件", "name": "主板", "amount": "10"},
                                                        {"category": category, "name": name, "amount": "6"}])
                flags = dict.fromkeys(OPTIONS, False)
                self.assertEqual(quote(sheet, **flags)["minimum_tax_price"], "13.75")
                self.assertEqual(quote(sheet, **{**flags, first: True, second: True})["minimum_tax_price"], "22")
                for partial in (first, second):
                    scope = Scope(**{**flags, partial: True})
                    self.assertIn("第2行", scope_quote(sheet, scope, details=True)["problem"])
                    public = scope_quote(sheet, scope, details=False)
                    self.assertIsNone(public["minimum_tax_price"])
                    self.assertNotIn(name, public["problem"])

    def test_single_charge_exclusions_and_explicit_classification_take_precedence(self):
        items = [{"category": "焊接组件", "name": "主板", "amount": "10"},
                 {"category": "外包装", "name": "外箱（不含运费）", "amount": "4"},
                 {"category": "内包装", "name": "内盒+外箱", "quote_role": "outer", "amount": "2"}]
        flags = {"ecoat": False, "inner": False, "outer": True, "freight": False}
        self.assertEqual(quote(model(items=items), **flags)["minimum_tax_price"], "22")
        items.append({"category": "外包装", "name": "主板+外箱+运费", "amount": "2"})
        self.assertIn("第4行", quote(model(items=items), **dict.fromkeys(OPTIONS, False))["problem"])

    def test_conflicting_named_charge_and_category_cannot_be_priced_as_one_option(self):
        for name, category in (("外箱", "内包装"), ("成品运费", "内包装"), ("包装费含运费", "运费")):
            with self.subTest(name=name):
                sheet = model(items=[{"name": "主板", "amount": "10"},
                                     {"name": name, "category": category, "amount": "3"}])
                flags = {"ecoat": False, "inner": True, "outer": False, "freight": False}
                self.assertIn("第2行", quote(sheet, **flags)["problem"])

    def test_assembly_coatings_do_not_contribute_to_welded_body_price(self):
        for category in ("球头", "球头1", "球头２", "大衬套", "小衬套"):
            with self.subTest(category=category):
                sheet = model(items=[{"name": "主板", "amount": "10"},
                                     {"name": "电泳", "category": "表面处理", "amount": "2"},
                                     {"name": "电泳", "category": category, "amount": "3"}])
                flags = {"inner": False, "outer": False, "freight": False}
                self.assertEqual(quote(sheet, stage="weld", **flags)["minimum_tax_price"], "16.5")
                self.assertEqual(quote(sheet, stage="finished", ecoat=False, **flags)["minimum_tax_price"], "17.88")

    def test_customer_delivery_and_unspecified_transport_are_not_charged_as_product_cost(self):
        items = [{"name": "主板", "amount": "10"}, {"name": "客户快递费", "category": "物流", "amount": "6"}]
        flags = {"ecoat": False, "inner": False, "outer": False}
        self.assertEqual(quote(model(items=items), freight=False, **flags)["minimum_tax_price"], "13.75")
        self.assertEqual(quote(model(items=items), freight=True, **flags)["minimum_tax_price"], "22")
        for name, category in (("配送费", "焊接组件"), ("供应商进货快递费", "运费")):
            items[1].update(name=name, category=category)
            self.assertIn("第2行", quote(model(items=items), freight=False, **flags)["problem"])

    def test_actual_scrap_only_follows_retained_components_and_old_blank_scrap(self):
        sheet = model()
        sheet.update(scrap_mode="auto", scrap_credit_override=None, scrap_credit="5", scrap_unit_price="2", scrap_complete=True)
        sheet["items"][0].update(scrap_total_weight="2", scrap_expected=True)
        sheet["items"][3].update(scrap_total_weight="1", scrap_expected=True)
        self.assertEqual(quote(sheet, outer=False)["scrap_credit"], "5")
        self.assertEqual(quote(sheet, stage="weld")["scrap_credit"], "3")
        sheet.update(scrap_credit_override="5")
        self.assertIn("废料抵减", quote(sheet, stage="weld")["problem"])
        sheet.update(scrap_mode="legacy")
        self.assertIn("废料抵减", quote(sheet, stage="weld")["problem"])
        sheet["source_info"] = {"scrap_reference_rows": [{"name": "主板"}]}
        self.assertEqual(quote(sheet, stage="weld")["scrap_credit"], "5")
        sheet.update(scrap_mode="auto", scrap_complete=False, scrap_credit_override=None, scrap_credit=None,
                     theoretical_scrap=None, net_cost=None)
        sheet["items"][0].update(scrap_total_weight=None, scrap_issue="待取重")
        self.assertEqual(quote(displayed_cost(sheet), stage="weld")["scrap_credit"], "0")

    def test_query_protocol_and_read_only_paginated_public_projection(self):
        self.assertEqual(Scope.from_query({"stage": ["weld"], "inner": ["1"], "outer": ["0"]}),
                         Scope("weld", False, True, False, False))
        for query in ({"other": ["1"]}, {"stage": ["bad"]}, {"stage": ["finished"], "inner": ["1", "0"]},
                      {"stage": ["finished"], "ecoat": ["yes"]}):
            with self.assertRaises(CostingValidationError):
                Scope.from_query(query)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite"
            initialize_costing_database(path)
            service = CostingService(path)
            records = [service.create(model(code=code, kind=kind), actor="tester") for code, kind in (
                ("A1", "arm"), ("A2", "arm"), ("A3", "ball"))]
            expected_versions = [(len(service.revisions(r["id"])), service.get(r["id"])) for r in records]
            with patch.object(SQLiteCostingRepository, "get_many", autospec=True, side_effect=SQLiteCostingRepository.get_many) as many:
                result = service.list(page_size=2, page=2, quote_scope=Scope(stage="weld"), summary_only=True)
                self.assertEqual(list(result["quotes"]), ["A3"])
                self.assertIn("不适用", result["quotes"]["A3"]["problem"])
                self.assertEqual(many.call_count, 1)
                self.assertEqual(many.call_args.args[1], [records[-1]["id"]])
            result = service.list(quote_scope=Scope(), summary_only=True)["quotes"]
            for value in result.values():
                self.assertEqual(set(value), {"label", "problem", "minimum_tax_price"})
                self.assertNotIn("内部胶套", json.dumps(value, ensure_ascii=False))
            self.assertNotIn("quotes", service.list())
            self.assertEqual(expected_versions, [(len(service.revisions(r["id"])), service.get(r["id"])) for r in records])
