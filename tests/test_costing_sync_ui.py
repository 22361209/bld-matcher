from __future__ import annotations

import io
import re

from tests.web_app_test_base import WebAppTestBase


class CostingSyncWebPreviewTest(WebAppTestBase):
    def test_costing_preview_distinguishes_locked_models_and_optional_reference_replacement(self):
        from app.database import connect
        from app.modules.business_sync.infrastructure import BusinessSyncRepository
        from app.modules.business_sync.service import BusinessSyncService
        from app.modules.costing.service import CostingService

        self.login()
        source_db = self.root / "other-site.sqlite3"
        with connect(source_db):
            pass
        source = CostingService(source_db)
        local = self.web.app.extensions["costing_service"]
        common = {"code": "SYNC-UI-CONFLICT", "items": [{"name": "示例工序", "amount": "12"}]}
        source.import_records([common, {**common, "code": "SYNC-UI-NEW"}], actor="源端",
                              references={"rates": [{"name": "示例工序", "amount": "15"}]})
        existing = local.import_records([common], actor="当前端",
                                        references={"rates": [{"name": "示例工序", "amount": "10"}]})["records"][0]
        package = BusinessSyncService(BusinessSyncRepository(source_db, allow_costing=True)).export(
            output_path=self.root / "costs-ui.tar.gz", selected=("costing",), actor="源端",
        )
        preview = self.client.post("/business-data-sync/preview", data={
            "package": (io.BytesIO(package.read_bytes()), "costs-ui.tar.gz"),
        })
        self.assertEqual(preview.status_code, 200)
        html = preview.get_data(as_text=True)
        self.assertIn('id="import-preview-costing"', html)
        self.assertIn("2 张成本单、2 行明细、2 条修改记录及参考资料", html)
        self.assertIn("SYNC-UI-CONFLICT", html)
        self.assertIn("有冲突的成本单保留当前端版本", html)
        self.assertIn("SYNC-UI-NEW", html)
        choices = re.findall(r'<input type="checkbox" name="use_package"[^>]*>', html)
        self.assertEqual(len(choices), 1)
        self.assertIn('value="costing:references"', choices[0])
        self.assertNotIn("checked", choices[0])
        self.assertNotIn('data-bulk-action="use-package" data-dataset="costing"', html)
        self.assertIn("确认按所选规则导入", html)
        self.assertEqual(local.get(existing["id"]), existing)
        self.assertEqual(local.list(q="SYNC-UI-NEW")["total"], 0)
