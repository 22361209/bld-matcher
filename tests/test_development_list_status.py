from html import unescape
import re
from urllib.parse import parse_qs, urlsplit

from tests.web_app_test_base import WebAppTestBase


class DevelopmentListStatusTest(WebAppTestBase):
    def test_worksheet_status_filter_preserves_search_and_pagination(self):
        from app.modules.development.factory import get_development_service
        from app.modules.development.worksheet import default_worksheet

        self.login()
        service = get_development_service()
        for index in range(31):
            flags = {"started": True, "completed": True} if index % 2 else {"started": True}
            service.create_worksheet({**default_worksheet(), "core_code": f"FILTER-QA-{index:02}", **flags}, actor="007")
        service.create_worksheet({**default_worksheet(), "core_code": "FILTER-QA-WAIT"}, actor="007")
        service.create_worksheet({**default_worksheet(), "core_code": "OUTSIDE-QA", "started": True}, actor="007")

        query = {"q": "FILTER-QA", "status": ["in_progress", "completed"]}
        first = self.client.get("/development", query_string=query).get_data(as_text=True)
        self.assertNotIn('<select name="status">', first)
        self.assertNotIn('type="submit">搜索</button>', first)
        self.assertIn('aria-label="筛选开发状态，已选择 2 项"', first)
        self.assertIn('<span>未开始</span><small>1</small>', first)
        self.assertIn('<span>已开始</span><small>16</small>', first)
        self.assertIn('<span>已完成</span><small>15</small>', first)
        self.assertIn('<td>已完成</td>', first)
        self.assertNotIn("FILTER-QA-WAIT", first)
        self.assertNotIn("OUTSIDE-QA", first)
        self.assertNotIn("部分完成", first)
        form = first.split('<form method="get" class="search-form"', 1)[1].split("</form>", 1)[0]
        self.assertEqual(re.findall(r'name="status" value="([^"]+)"', form), query["status"])
        next_url = unescape(re.search(r'href="([^"]+)">下一页</a>', first)[1])
        self.assertEqual(parse_qs(urlsplit(next_url).query), {"q": ["FILTER-QA"], "status": query["status"], "page": ["2"]})
        second = self.client.get(next_url).get_data(as_text=True)
        def shown(page):
            return set(re.findall(r"<strong>(FILTER-QA-[^<]+)</strong>", page))

        self.assertEqual(len(shown(first) | shown(second)), 31)

        single = self.client.get("/development?q=FILTER-QA&status=completed").get_data(as_text=True)
        self.assertEqual(single.count("<td>已完成</td>"), 15)
        self.assertNotIn("<td>已开始</td>", single)
        legacy = self.client.get("/development?type=legacy&status=partial_completed").get_data(as_text=True)
        self.assertIn('<option value="partial_completed" selected>部分完成</option>', legacy)
        self.assertNotIn('data-filter-key="status"', legacy)
