from __future__ import annotations

import json
import html
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from app.modules.costing.domain import CostingValidationError
from app.modules.costing.grid import COLUMN_FILTER_FIELDS, EMPTY_FILTER_VALUE, format_cost_money
from app.modules.costing.repository import SQLiteCostingUnitOfWork, initialize_costing_database
from app.modules.costing.service import CostingService
from app.modules.costing.trial_app import create_trial_app, initialize_trial


class CostingFiltersTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "costs.sqlite3"
        initialize_costing_database(self.path)
        self.service = CostingService(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def sheet(code, amount="80", **overrides):
        return {"code": code, "kind": "arm", "name": "摆臂", "models": "车型 A",
                "items": [{"name": "已核定明细", "mode": "fixed", "amount": amount}], **overrides}

    def test_filters_scan_full_dataset_before_pagination_and_keep_code_order(self):
        self.service.import_records([
            self.sheet(f"K{index:03}", "80" if index % 2 else "160") for index in range(125)
        ], actor="tester")
        result = self.service.list(column_filters={"minimum_price": ["110"]}, page=2)
        self.assertEqual((result["total"], result["pages"], result["page"]), (62, 2, 2))
        self.assertEqual([row["code"] for row in result["records"]], [f"K{index:03}" for index in range(101, 125, 2)])
        self.assertTrue(all(row["items"] for row in result["records"]))
        last = self.service.list(column_filters={"code": ["K124"]})
        self.assertEqual([row["code"] for row in last["records"]], ["K124"])
        descending = self.service.list(column_filters={"minimum_price": ["110"]}, sort="code_desc", page_size=2)
        self.assertEqual([row["code"] for row in descending["records"]], ["K123", "K121"])

    def test_each_business_column_and_combination_filter(self):
        target = self.service.create(self.sheet("K001", "80", approved_cost="100", scrap_credit="20",
                                               suggested_tax_price_override="130"), actor="tester")
        self.service.create(self.sheet("Q001", "32", kind="ball", name="球头", models="车型 B"), actor="tester")
        expected = {"code": "K001", "name": "摆臂 / 车型 A", "kind": "arm", "calculated": "80",
                    "approved": "100", "scrap": "20", "net": "80", "minimum_price": "110",
                    "suggested_price": "130", "status": "ready"}
        self.assertEqual(set(expected), set(COLUMN_FILTER_FIELDS))
        for field, value in expected.items():
            with self.subTest(field=field):
                result = self.service.list(column_filters={field: [value]}, kind="arm")
                self.assertEqual([row["id"] for row in result["records"]], [target["id"]])
        combined = self.service.list(q="车型", column_filters={key: [value] for key, value in expected.items()})
        self.assertEqual(combined["total"], 1)
        self.assertEqual(self.service.list(column_filters={"kind": ["arm"], "net": ["32"]})["total"], 0)
        self.assertEqual(self.service.list(column_filters={"kind": ["arm", "ball"]})["total"], 2)

    def test_same_displayed_vehicle_groups_all_matching_sheets_and_accepts_old_filter_urls(self):
        self.service.import_records([self.sheet("GROUP1", models="活动胶套"), self.sheet("GROUP2", name="", models="活动胶套")],
                                    actor="tester")
        self.assertEqual(self.service.filter_options()["name"], [{"value": "活动胶套", "label": "活动胶套", "count": 2}])
        for value in ("活动胶套", "摆臂 / 活动胶套"):
            self.assertEqual(self.service.list(column_filters={"name": [value]})["total"], 2)

    def test_facets_honor_other_columns_and_include_full_dataset(self):
        self.service.import_records([
            self.sheet(f"K{index:03}", "80" if index % 2 else "160") for index in range(61)
        ] + [self.sheet("Q001", "32", kind="ball")], actor="tester")
        options = self.service.filter_options(column_filters={"kind": ["arm"], "minimum_price": ["110"]})
        self.assertEqual(options["minimum_price"], [
            {"value": "220", "label": "220", "count": 31}, {"value": "110", "label": "110", "count": 30},
        ])
        self.assertEqual(options["kind"], [{"value": "arm", "label": "控制臂", "count": 30}])
        self.assertEqual(len(options["code"]), 30)
        self.assertTrue(any(option["value"] == "K059" for option in options["code"]))
        # A no-longer-matching choice remains visible with count zero so it can be cleared.
        selected = self.service.filter_options(column_filters={"kind": ["arm"], "minimum_price": ["44"]})
        self.assertIn({"value": "44", "label": "44", "count": 0}, selected["minimum_price"])
        searched = self.service.filter_options(q="Q001")
        self.assertEqual(searched["code"], [{"value": "Q001", "label": "Q001", "count": 1}])

    def test_empty_values_are_selectable_and_zero_is_distinct(self):
        empty = self.service.create(self.sheet("EMPTY", name="", models="", items=[]), actor="tester")
        zero = self.service.create(self.sheet("ZERO", "0", approved_cost="0", scrap_credit="0"), actor="tester")
        for field in ("name", "calculated", "approved", "net", "minimum_price", "suggested_price"):
            with self.subTest(field=field):
                result = self.service.list(column_filters={field: [EMPTY_FILTER_VALUE]})
                self.assertEqual([row["id"] for row in result["records"]], [empty["id"]])
                self.assertIn({"value": EMPTY_FILTER_VALUE, "label": "（空）", "count": 1},
                              self.service.filter_options()[field])
        self.assertEqual(self.service.list(column_filters={"scrap": ["0"]})["total"], 2)
        result = self.service.list(column_filters={"minimum_price": ["0"], "status": ["ready"]})
        self.assertEqual([row["id"] for row in result["records"]], [zero["id"]])
        self.assertEqual(self.service.list(column_filters={"status": ["draft"]})["total"], 1)

    def test_amount_filters_use_same_rounding_and_grouping_as_display(self):
        self.service.import_records([
            self.sheet("A", "1234.5678901"), self.sheet("B", "1234.5678902"), self.sheet("C", "1234.567891"),
        ], actor="tester")
        displayed = format_cost_money("1234.5678901")
        self.assertEqual(displayed, "1,234.56789")
        result = self.service.list(column_filters={"net": [displayed]})
        self.assertEqual([row["code"] for row in result["records"]], ["A", "B"])
        self.assertIn({"value": displayed, "label": displayed, "count": 2}, self.service.filter_options()["net"])

    def test_legacy_prices_are_derived_without_writes_and_manual_prices_filter_effective_value(self):
        legacy = self.service.create(self.sheet("LEGACY", "80.001"), actor="tester")
        manual = self.service.create(self.sheet("MANUAL", "80.001", suggested_tax_price_override="123.45"), actor="tester")
        # This fixture represents a pre-price record. Read operations must not rewrite it or add a revision.
        old_payload = {key: value for key, value in legacy.items()
                       if key not in {"minimum_tax_price", "suggested_tax_price", "suggested_tax_price_override"}}
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE costing_records SET payload_json=? WHERE id=?", (json.dumps(old_payload), legacy["id"]))
        reopened = self.service.get(legacy["id"])
        self.assertEqual((reopened["minimum_tax_price"], reopened["suggested_tax_price"]), ("110.01", "110.01"))
        self.assertEqual(reopened["updated_at"], legacy["updated_at"])
        self.assertEqual(self.service.list(column_filters={"minimum_price": ["110.01"]})["total"], 2)
        result = self.service.list(column_filters={"suggested_price": ["123.45"]})
        self.assertEqual([row["id"] for row in result["records"]], [manual["id"]])
        self.assertEqual(self.service.list(column_filters={"suggested_price": ["110.01"]})["total"], 1)
        self.assertEqual(self.service.list(q="LEGACY")["records"][0]["minimum_tax_price"], "110.01")
        self.assertEqual(len(self.service.revisions(legacy["id"])), 1)
        with sqlite3.connect(self.path) as connection:
            unchanged = json.loads(connection.execute("SELECT payload_json FROM costing_records WHERE id=?", (legacy["id"],)).fetchone()[0])
        self.assertEqual(unchanged, old_payload)

    def test_summary_projection_excludes_details_but_preserves_decimal_precision(self):
        self.service.create(self.sheet("A", "12.34567890123456789"), actor="tester")
        with SQLiteCostingUnitOfWork(self.path) as uow:
            rows = uow.repository.summaries(q="", kind="", status="", sort="code")
        self.assertEqual(rows[0]["net_cost"], "12.34567890123456789")
        self.assertNotIn("items", rows[0])
        self.assertNotIn("source_info", rows[0])

    def test_invalid_filter_values_cannot_expand_result_or_execute_sql(self):
        self.service.create(self.sheet("K001"), actor="tester")
        self.assertEqual(self.service.list(column_filters={"code": ["K001' OR 1=1 --"]})["total"], 0)
        self.assertEqual(self.service.list(column_filters={"code": []})["total"], 1)
        for filters in ({"unknown": ["x"]}, {"code": [1]}, {"code": ["x" * 601]}, {"code": ["x"] * 5001}):
            with self.subTest(filters=str(filters)[:80]), self.assertRaises(CostingValidationError):
                self.service.list(column_filters=filters)
        self.assertEqual(self.service.list()["total"], 1)


class CostingFilterWebTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        self.password = "costing-filter-test-password"
        initialize_trial(directory, self.password)
        self.app = create_trial_app(directory)
        self.app.config["TESTING"] = True
        self.service = self.app.extensions["costing_service"]
        self.client = self.app.test_client()
        form = self.client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', form).group(1)
        self.client.post("/login", data={"password": self.password, "csrf_token": token})
        self.client.get("/costs/new")
        with self.client.session_transaction() as state:
            self.token = state["_csrf_token"]

    def tearDown(self):
        self.temporary.cleanup()

    def test_vehicle_cells_and_filter_labels_use_models_or_brand_for_generic_arm_names(self):
        cases = (
            ("C801102", "摆臂", "活动胶套", "现代", "活动胶套", ""),
            ("BRAND", "摆臂", "", "现代", "现代", ""),
            ("UNKNOWN", "摆臂", "", "", "—", ""),
            ("SPECIFIC", "下摆臂", "雅绅特", "现代", "下摆臂", "雅绅特"),
            ("BALL", "球头", "ix35", "现代", "球头", "ix35"),
        )
        self.service.import_records([
            CostingFiltersTest.sheet(code, name=name, models=models, brand=brand)
            for code, name, models, brand, _, _ in cases
        ], actor="tester")
        for code, name, models, brand, primary, secondary in cases:
            with self.subTest(code=code):
                # Previously bookmarked column values still select the same records.
                choice = " / ".join(filter(None, (name, models or brand)))
                body = self.client.get("/", query_string={"q": code, "cf_name": choice}).get_data(as_text=True)
                content = re.search(r'<td data-col="name">(.*?)</td>', body, re.S).group(1)
                self.assertIn(f"<span>{primary}</span>", content)
                if secondary:
                    self.assertIn(f'<small class="costing-secondary">{secondary}</small>', content)
                else:
                    self.assertNotIn("<small", content)
                label = "（空）" if primary == "—" else " / ".join(filter(None, (primary, secondary)))
                canonical = EMPTY_FILTER_VALUE if primary == "—" else label
                self.assertIn(f'value="{canonical}" checked><span>{label}</span>', body)
                row = self.service.list(q=code)["records"][0]
                self.assertEqual((row["name"], row["models"], row["brand"]), (name, models, brand))

    def test_literal_vehicle_names_and_legacy_urls_remain_distinguishable(self):
        records = [
            ("普通", "摆臂", "雅绅特", "雅绅特"), ("名称含分隔符", "摆臂 / 雅绅特", "", "摆臂 / 雅绅特"),
            ("车型叫摆臂", "", "摆臂", "摆臂"), ("未填", "摆臂", "", "（空）"),
            ("保留字", "", EMPTY_FILTER_VALUE, EMPTY_FILTER_VALUE),
        ]
        self.service.import_records([CostingFiltersTest.sheet(code, name=name, models=models)
                                     for code, name, models, _ in records], actor="tester")
        choices = {option["label"]: option["value"] for option in self.service.filter_options()["name"]}
        self.assertEqual(set(choices), {row[3] for row in records})
        for code, _, _, label in records:
            result = self.client.get("/", query_string={"cf_name": choices[label]},
                                     headers={"Accept": "application/json"}).get_json()["result"]
            self.assertEqual([row["code"] for row in result["records"]], [code])
        for legacy, expected in (("摆臂 / 雅绅特", "普通"), ("摆臂 / 摆臂", "车型叫摆臂"), ("摆臂", "未填")):
            result = self.client.get("/", query_string={"cf_name": legacy},
                                     headers={"Accept": "application/json"}).get_json()["result"]
            self.assertEqual([row["code"] for row in result["records"]], [expected])

    def test_filter_query_survives_search_pagination_and_json_refresh(self):
        self.service.import_records([
            CostingFiltersTest.sheet(f"K{index:03}", "80" if index % 2 else "160") for index in range(55)
        ] + [CostingFiltersTest.sheet("Q001", "80", kind="ball")], actor="tester")
        query = [("q", "车型"), ("kind", "arm"), ("cf_minimum_price", "110"), ("cf_minimum_price", "220")]
        response = self.client.get("/", query_string=query)
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        search_form = re.search(r'<form class="search-form".*?</form>', body, re.S).group(0)
        self.assertIn('name="cf_minimum_price" value="110"', search_form)
        self.assertIn('name="cf_minimum_price" value="220"', search_form)
        pagination_links = [html.unescape(link) for link in re.findall(r'href="([^"]+)"', body)
                            if "page=2" in link]
        self.assertTrue(pagination_links)
        for link in pagination_links:
            parsed = parse_qs(urlsplit(link).query)
            self.assertEqual(parsed["cf_minimum_price"], ["110", "220"])
            self.assertEqual(parsed["q"], ["车型"])
            self.assertEqual(parsed["kind"], ["arm"])
        result = self.client.get(pagination_links[0], headers={"Accept": "application/json"}).get_json()["result"]
        self.assertEqual((result["page"], result["total"]), (2, 55))
        self.assertEqual([row["code"] for row in result["records"]], [f"K{index:03}" for index in range(50, 55)])
        # An exact value after the original first page is still queryable through the public route.
        exact = self.client.get("/?cf_code=K054", headers={"Accept": "application/json"}).get_json()["result"]
        self.assertEqual([row["code"] for row in exact["records"]], ["K054"])
        self.assertEqual(body.count("data-column-filter-trigger"), 10)
        self.assertEqual(body.count("data-column-drag-handle"), 11)
        self.assertIn('data-column-drag-handle data-column-label="操作"', body)
        self.assertIn('data-column-order-status', body)

    def test_only_manual_price_input_is_writable_and_clear_restores_default(self):
        headers = {"X-CSRF-Token": self.token, "Accept": "application/json"}
        raw = {**CostingFiltersTest.sheet("A"), "minimum_tax_price": "1", "suggested_tax_price": "2",
               "suggested_tax_price_override": "120"}
        created = self.client.post("/costs/new", json=raw, headers=headers).get_json()["record"]
        self.assertEqual((created["minimum_tax_price"], created["suggested_tax_price"]), ("110", "120"))
        preview = self.client.post("/costs/preview", json={**created, "suggested_tax_price_override": "125"},
                                   headers=headers).get_json()["record"]
        self.assertEqual(preview["suggested_tax_price"], "125")
        self.assertEqual(self.service.get(created["id"])["suggested_tax_price"], "120")
        updated = self.client.post(f"/costs/{created['id']}", json={"version": 1, "suggested_tax_price_override": "",
                                    "minimum_tax_price": "1", "suggested_tax_price": "2"}, headers=headers)
        self.assertEqual(updated.status_code, 200)
        record = updated.get_json()["record"]
        self.assertEqual((record["minimum_tax_price"], record["suggested_tax_price"]), ("110", "110"))
        self.assertIsNone(record["suggested_tax_price_override"])
        self.assertEqual(self.service.get(created["id"]), record)

    def test_invalid_column_filter_returns_validation_error(self):
        response = self.client.get("/", query_string={"cf_code": "x" * 601}, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(response.get_json()["error"], "列筛选值无效。")

    def test_retained_factory_keeps_error_mapping_after_module_cache_reset(self):
        # Other application fixtures reload app.* while retaining imported factories.
        # A retained factory must not mix old error handlers with newly imported routes.
        with patch.dict(sys.modules):
            for name in tuple(sys.modules):
                if name == "app.modules.costing" or name.startswith("app.modules.costing."):
                    sys.modules.pop(name)
            app = create_trial_app(self.service.db_path.parent)
            client = app.test_client()
            with client.session_transaction() as state:
                state["costing_authenticated"] = app.config["COSTING_CREDENTIAL_ID"]
            response = client.get("/", query_string={"cf_code": "x" * 601}, headers={"Accept": "application/json"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.get_json()["error"], "列筛选值无效。")
