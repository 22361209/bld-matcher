from __future__ import annotations

import io
import re
from html import unescape

from tests.web_app_test_base import WebAppTestBase


class CostSyncPermissionTest(WebAppTestBase):
    def test_current_cost_permissions_govern_preview_apply_and_saved_downloads(self):
        from app.modules.admin.factory import get_admin_service

        admin = get_admin_service()
        permissions = {"sync_product_data": "allow", "generate_match": "allow",
                       "view_costs": "allow", "manage_costs": "deny"}
        user_id = admin.save_user({
            "username": "qa-cost-sync", "password": "fixture-password", "display_name": "测试同步员",
            "role": "viewer", "active": "1", "permission_overrides": permissions,
        }, actor="007")
        self.web.app.extensions["costing_service"].create(
            {"code": "QA-SYNC-SEC", "items": [{"name": "测试费用", "amount": "12"}]}, actor="007",
        )
        client = self.web.app.test_client()
        client.post("/login", data={"username": "qa-cost-sync", "password": "fixture-password"})
        with client.post("/business-data-sync/export", data={"dataset": ["customers", "costing"]}) as export:
            self.assertEqual(export.status_code, 200)
            package = export.data
        saved_package = next((self.root / "outputs").rglob("business-data-*.tar.gz"))
        download = "/download/" + saved_package.relative_to(self.root / "outputs").as_posix()
        with client.get(download) as previous_export:
            self.assertEqual(previous_export.data, package)

        def preview():
            return client.post("/business-data-sync/preview", data={"package": (io.BytesIO(package), "qa.tar.gz")})

        response = preview()
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        uploaded = next((self.root / "uploads").rglob("*.tar.gz"))
        # Even a forged form containing the actual package path and a token cannot grant write access.
        token = re.search(r'name="preview_token" value="([^"]+)"', html)
        form = {"package_path": str(uploaded), "preview_token": unescape(token[1]) if token else "forged"}
        self.assertEqual(client.post("/business-data-sync/apply", data=form).status_code, 403)

        admin.update_user_overrides(user_id, {**permissions, "view_costs": "deny", "manage_costs": "allow"}, actor="007")
        self.assertNotIn('value="costing"', client.get("/business-data-sync").get_data(as_text=True))
        self.assertEqual(client.post("/business-data-sync/export", data={"dataset": "costing"}).status_code, 403)
        self.assertEqual(preview().status_code, 403)
        self.assertEqual(client.post("/business-data-sync/apply", data=form).status_code, 403)
        self.assertEqual(client.get(download).status_code, 403)
        # Case-insensitive filesystems also accept alternate spellings of the same path.
        upper_name = saved_package.name.upper()
        uppercase = saved_package.with_name(upper_name)
        if not uppercase.exists():
            uppercase.write_bytes(package)
        self.assertEqual(client.get(download.replace(saved_package.name, upper_name)).status_code, 403)
        with client.post("/business-data-sync/export", data={"dataset": "customers"}) as other_export:
            self.assertEqual(other_export.status_code, 200)
