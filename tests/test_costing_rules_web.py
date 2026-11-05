from __future__ import annotations

import re
from uuid import uuid4

from tests.web_app_test_base import WebAppTestBase


class CostingRulesWebTest(WebAppTestBase):
    def test_rules_navigation_and_content_follow_cost_permissions(self):
        from app.modules.admin.factory import get_admin_service

        self.client = self.web.app.test_client()
        anonymous = self.client.get("/costs/rules", headers={"Accept": "application/json"})
        self.assertEqual(anonymous.status_code, 401)
        self.login()
        with self.client.session_transaction() as state:
            actor = state["user_id"]
        levels = ("view_costs", "view_cost_list_full", "view_cost_details", "manage_costs")
        for level in range(5):
            with self.subTest(level=level):
                name = "rules-reader-" + uuid4().hex[:8]
                get_admin_service().save_user(
                    {"username": name, "display_name": name, "password": "rules-reader-test",
                     "role": "viewer", "active": "1", "permission_overrides": dict.fromkeys(levels[:level], "allow")},
                    actor="007", actor_user_id=actor,
                )
                client = self.web.app.test_client()
                self.assertEqual(client.post("/login", data={"username": name, "password": "rules-reader-test"}).status_code, 302)
                response = client.get("/costs/rules", headers={"Accept": "application/json"} if level == 0 else {})
                if level == 0:
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn('href="/costs/rules"', client.get("/products").get_data(as_text=True))
                    continue
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                nav = re.search(r"<nav\b.*?</nav>", body, re.S).group()
                self.assertIn('href="/costs">成本核算</a>', nav)
                self.assertIn('aria-current="page" href="/costs/rules">核算规则</a>', nav)
                self.assertNotIn("展开成本核算导航", nav)
                self.assertNotIn("<button", nav.split("data-nav-link-menu>", 1)[1].split("</div>", 1)[0])
                self.assertIn('data-page="costing.rules"', body)
                for label in ("最低含税售价", "建议含税单价", "元/件"):
                    self.assertIn(label, body)
                for explanation in ("售价使用说明", "含税售价参考下限", "请有成本明细权限的同事核对"):
                    self.assertEqual(explanation in body, level == 1)
                for detail in ("直接金额", "按件计算", "按重量计算", "按重量运费", "包装分摊", "调整 / 返还",
                               "实际废料抵减额", "27.50", "自动抵减暂按 0", "左右共用件", "左右对应件", "本侧独有",
                               "抵减后成本", "÷ 0.8", "× 1.1", "7.85"):
                    self.assertEqual(detail in body, level >= 2, detail)
                self.assertNotIn("data-record=", body)
