from __future__ import annotations

from datetime import timedelta

from tests.web_app_test_base import (
    WebAppTestBase,
    Path,
    re,
)


class TestWebAccessAndShell(WebAppTestBase):
    def test_login_and_homepage(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        login_html = response.get_data(as_text=True)
        self.assertIn('name="client_mode"', login_html)
        self.assertIn("pages/login.js", login_html)

        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("BLD", html)
        self.assertIn('class="search-command"', html)
        self.assertIn('class="embedded-submit" type="submit">开始查询', html)
        self.assertNotIn("当前目录：", html)
        self.assertNotIn("上传客户询价表，自动在产品目录中新增匹配的 BLD NO.", html)
        self.assertIn('class="embedded-input-control"', html)
        self.assertIn('class="embedded-submit" type="submit">搜索', html)
        nav_order = ["询价处理", "报价记录", "合同管理", "产品目录", "管件资料", "生产料单", "物料图纸"]
        nav_positions = [html.index(label) for label in nav_order]
        self.assertEqual(nav_positions, sorted(nav_positions))
        self.assertNotIn("发货通知", html)
        self.assertNotIn("货物识别", html)
        self.assertIn('class="search-hero"', html)
        self.assertNotIn('class="workspace-header"', html)
        self.assertIn('class="nav-menu contract-nav-menu nav-mobile-secondary" data-nav-menu', html)
        self.assertIn('aria-controls="mobile-workspace-nav" data-mobile-workspace-trigger>BLD 工作台', html)
        self.assertIn(
            'type="button" aria-haspopup="menu" aria-expanded="false" aria-label="选择合同类型" data-nav-menu-trigger>合同管理</button>',
            html,
        )
        self.assertIn('href="/contracts" role="menuitem">采购合同</a>', html)
        self.assertIn('href="/contracts/sales" role="menuitem">销售合同</a>', html)

    def test_account_can_choose_default_landing_page_and_login_session_lasts_seven_days(self):
        def restore_default_page():
            self.client.post("/logout")
            with self.web.connect(self.web.DB_PATH) as connection:
                connection.execute(
                    """
                    UPDATE users
                    SET default_page = '', default_mobile_page = ''
                    WHERE username = '007'
                    """
                )
                connection.commit()

        self.addCleanup(restore_default_page)
        self.login()
        settings = self.client.get("/account/password")
        settings_html = settings.get_data(as_text=True)
        self.assertEqual(settings.status_code, 200)
        self.assertIn("桌面端登录后默认页面", settings_html)
        self.assertIn("移动端登录后默认页面", settings_html)
        self.assertIn('<option value="quotes">报价记录</option>', settings_html)
        self.assertIn("登录状态最长保留 7 天", settings_html)

        saved = self.client.post(
            "/account/default-page",
            data={"default_page": "quotes", "default_mobile_page": "products"},
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as connection:
            default_pages = connection.execute(
                "SELECT default_page, default_mobile_page FROM users WHERE username = '007'"
            ).fetchone()
        self.assertEqual(default_pages["default_page"], "quotes")
        self.assertEqual(default_pages["default_mobile_page"], "products")

        self.client.post("/logout")
        login = self.client.post(
            "/login",
            data={"username": "007", "password": "test-admin-pw", "client_mode": "desktop"},
            follow_redirects=False,
        )
        self.assertEqual(login.location, "/quotes")
        self.assertEqual(self.web.app.permanent_session_lifetime, timedelta(days=7))
        self.assertIn("Expires=", login.headers.get("Set-Cookie", ""))
        with self.client.session_transaction() as current_session:
            self.assertTrue(current_session.permanent)

        self.client.post("/logout")
        mobile_login = self.client.post(
            "/login",
            data={"username": "007", "password": "test-admin-pw", "client_mode": "mobile"},
            follow_redirects=False,
        )
        self.assertEqual(mobile_login.location, "/products")

        self.client.post("/logout")
        mobile_fallback = self.client.post(
            "/login",
            data={"username": "007", "password": "test-admin-pw"},
            headers={"User-Agent": "Mozilla/5.0 (iPhone; Mobile)"},
            follow_redirects=False,
        )
        self.assertEqual(mobile_fallback.location, "/products")

        rejected = self.client.post(
            "/account/default-page",
            data={"default_page": "not-a-real-page", "default_mobile_page": "products"},
            follow_redirects=True,
        )
        self.assertIn("该页面不存在或当前账号没有访问权限", rejected.get_data(as_text=True))
        with self.web.connect(self.web.DB_PATH) as connection:
            stored_after_rejection = connection.execute(
                "SELECT default_page, default_mobile_page FROM users WHERE username = '007'"
            ).fetchone()
        self.assertEqual(stored_after_rejection["default_page"], "quotes")
        self.assertEqual(stored_after_rejection["default_mobile_page"], "products")

    def test_admin_can_manage_roles_and_account_permission_overrides(self):
        role_name = "WEB 询价专员"
        username = "web-role-specialist"

        def cleanup_access_records():
            self.client.post("/logout")
            with self.web.connect(self.web.DB_PATH) as connection:
                user = connection.execute(
                    "SELECT id FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
                if user:
                    connection.execute(
                        "DELETE FROM user_permission_overrides WHERE user_id = ?",
                        (user["id"],),
                    )
                    connection.execute("DELETE FROM users WHERE id = ?", (user["id"],))
                role = connection.execute(
                    "SELECT role_key FROM roles WHERE name = ?",
                    (role_name,),
                ).fetchone()
                if role:
                    connection.execute(
                        "DELETE FROM role_permissions WHERE role_key = ?",
                        (role["role_key"],),
                    )
                    connection.execute(
                        "DELETE FROM roles WHERE role_key = ?",
                        (role["role_key"],),
                    )
                connection.commit()

        self.addCleanup(cleanup_access_records)
        self.login()
        default_page = self.client.get("/users")
        default_html = default_page.get_data(as_text=True)
        self.assertEqual(default_page.status_code, 200)
        self.assertIn("账号列表", default_html)
        self.assertNotIn("账号个人权限", default_html)

        account_page = self.client.get("/users", query_string={"view": "accounts"})
        account_html = account_page.get_data(as_text=True)
        self.assertEqual(account_page.status_code, 200)
        self.assertIn("账号权限管理", account_html)
        self.assertIn('name="user_id"', account_html)
        self.assertIn('href="/users?view=roles"', account_html)

        role_page = self.client.get("/users", query_string={"view": "role-list"})
        role_html = role_page.get_data(as_text=True)
        self.assertEqual(role_page.status_code, 200)
        self.assertIn("新增角色", role_html)
        self.assertIn("系统固定", role_html)
        self.assertNotIn('name="description"', role_html)

        created_role = self.client.post(
            "/roles/save",
            data={
                "name": role_name,
                "permission_selection_present": "1",
                "permissions": ["generate_match", "manage_aliases", "sync_product_data"],
            },
            follow_redirects=False,
        )
        self.assertEqual(created_role.status_code, 302)
        self.assertIn("view=roles", created_role.headers["Location"])
        with self.web.connect(self.web.DB_PATH) as connection:
            role = connection.execute(
                "SELECT role_key FROM roles WHERE name = ?",
                (role_name,),
            ).fetchone()
        self.assertIsNotNone(role)
        role_key = role["role_key"]

        created_user = self.client.post(
            "/users/save",
            data={
                "username": username,
                "display_name": "Web 权限专员",
                "password": "specialist-password",
                "role": role_key,
                "active": "1",
                "permission_selection_present": "1",
                "permission_generate_match": "deny",
                "permission_view_customer_prices": "allow",
            },
            follow_redirects=False,
        )
        self.assertEqual(created_user.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as connection:
            user = connection.execute(
                "SELECT id FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            overrides = {
                row["permission"]: row["effect"]
                for row in connection.execute(
                    "SELECT permission, effect FROM user_permission_overrides WHERE user_id = ?",
                    (user["id"],),
                ).fetchall()
            }
        self.assertEqual(
            overrides,
            {"generate_match": "deny", "view_customer_prices": "allow"},
        )

        editing = self.client.get(f"/users/{user['id']}/edit")
        editing_html = editing.get_data(as_text=True)
        self.assertEqual(editing.status_code, 200)
        self.assertIn(f'value="{role_key}" selected', editing_html)
        self.assertNotIn('name="permission_generate_match"', editing_html)

        permissions_page = self.client.get(
            "/users", query_string={"view": "accounts", "user_id": user["id"]}
        )
        permissions_html = permissions_page.get_data(as_text=True)
        self.assertEqual(permissions_page.status_code, 200)
        self.assertIn("账号个人权限", permissions_html)
        self.assertIn('name="permission_generate_match" value="deny" checked', permissions_html)

        blocked_delete = self.client.post(
            f"/roles/{role_key}/delete",
            follow_redirects=False,
        )
        self.assertEqual(blocked_delete.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as connection:
            self.assertIsNotNone(
                connection.execute("SELECT 1 FROM roles WHERE role_key = ?", (role_key,)).fetchone()
            )

        self.client.post("/logout")
        login = self.client.post(
            "/login",
            data={"username": username, "password": "specialist-password", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)
        homepage = self.client.get("/", follow_redirects=True).get_data(as_text=True)
        self.assertIn(f"{username} · {role_name}", homepage)
        self.assertIn("报价记录", homepage)
        self.assertIn("业务数据同步", homepage)

        quote_page = self.client.get("/quotes", follow_redirects=False)
        self.assertEqual(quote_page.status_code, 200)
        denied_match = self.client.post(
            "/match",
            data={"quick_oe": "TEST"},
            follow_redirects=False,
        )
        self.assertEqual(denied_match.status_code, 302)
        self.assertTrue(denied_match.headers["Location"].endswith("/quotes"))
        denied_admin = self.client.get("/users", follow_redirects=False)
        self.assertEqual(denied_admin.status_code, 302)
        self.assertTrue(denied_admin.headers["Location"].endswith("/quotes"))

        with self.web.connect(self.web.DB_PATH) as connection:
            actions = {
                row["action"]
                for row in connection.execute(
                    "SELECT action FROM audit_logs WHERE target_key IN (?, ?)",
                    (role_key, username),
                ).fetchall()
            }
        self.assertIn("新增角色", actions)
        self.assertIn("新增账号", actions)

    def test_user_changes_own_password_with_old_password_verified(self):
        self.login()
        with self.web.connect(self.web.DB_PATH) as connection:
            role = connection.execute(
                "SELECT role_key FROM roles WHERE is_system = 0 ORDER BY role_key LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(role)
        username = "self-password-user"

        def cleanup_user():
            self.client.post("/logout")
            with self.web.connect(self.web.DB_PATH) as connection:
                row = connection.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()
                if row:
                    connection.execute(
                        "DELETE FROM user_permission_overrides WHERE user_id = ?", (row["id"],)
                    )
                    connection.execute("DELETE FROM users WHERE id = ?", (row["id"],))
                    connection.commit()

        self.addCleanup(cleanup_user)
        created = self.client.post(
            "/users/save",
            data={
                "username": username,
                "password": "initial-pw-1",
                "role": role["role_key"],
                "active": "1",
                "permission_selection_present": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        self.client.post("/logout")

        login = self.client.post(
            "/login",
            data={"username": username, "password": "initial-pw-1", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)

        homepage = self.client.get("/").get_data(as_text=True)
        self.assertIn('href="/account/password"', homepage)
        self.assertIn("账号设置", homepage)

        form_page = self.client.get("/account/password")
        form_html = form_page.get_data(as_text=True)
        self.assertEqual(form_page.status_code, 200)
        self.assertIn('data-page="account.password"', form_html)
        self.assertIn('name="old_password"', form_html)
        self.assertIn('name="new_password"', form_html)
        self.assertIn('name="confirm_password"', form_html)

        mismatch = self.client.post(
            "/account/password",
            data={
                "old_password": "initial-pw-1",
                "new_password": "updated-pw-1",
                "confirm_password": "updated-pw-2",
            },
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn("两次输入的新密码不一致", mismatch.get_data(as_text=True))

        wrong_old = self.client.post(
            "/account/password",
            data={
                "old_password": "not-the-password",
                "new_password": "updated-pw-1",
                "confirm_password": "updated-pw-1",
            },
        )
        self.assertEqual(wrong_old.status_code, 400)
        self.assertIn("原密码不正确", wrong_old.get_data(as_text=True))

        changed = self.client.post(
            "/account/password",
            data={
                "old_password": "initial-pw-1",
                "new_password": "updated-pw-1",
                "confirm_password": "updated-pw-1",
            },
            follow_redirects=False,
        )
        self.assertEqual(changed.status_code, 302)

        self.client.post("/logout")
        old_login = self.client.post(
            "/login",
            data={"username": username, "password": "initial-pw-1", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(old_login.status_code, 302)
        self.assertIn("/login", old_login.headers["Location"])
        new_login = self.client.post(
            "/login",
            data={"username": username, "password": "updated-pw-1", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(new_login.status_code, 302)

        with self.web.connect(self.web.DB_PATH) as connection:
            actions = {
                row["action"]
                for row in connection.execute(
                    "SELECT action FROM audit_logs WHERE target_key = ?", (username,)
                ).fetchall()
            }
        self.assertIn("修改密码", actions)

    def test_admin_account_page_never_defaults_to_admin_when_roles_are_missing(self):
        self.login()
        with self.web.connect(self.web.DB_PATH) as connection:
            roles = connection.execute(
                """
                SELECT role_key, name, description, is_system, created_at, updated_at
                FROM roles
                WHERE is_system = 0
                """
            ).fetchall()
            permissions = connection.execute(
                """
                SELECT role_key, permission, created_at
                FROM role_permissions
                WHERE role_key != 'admin'
                """
            ).fetchall()
            connection.execute("DELETE FROM role_permissions WHERE role_key != 'admin'")
            connection.execute("DELETE FROM roles WHERE is_system = 0")
            connection.commit()

        try:
            response = self.client.get("/users")
            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn("请先创建非管理员角色", html)
            self.assertNotIn('action="/users/save"', html)
            self.assertIn('href="/users?view=role-list"', html)
        finally:
            with self.web.connect(self.web.DB_PATH) as connection:
                connection.executemany(
                    """
                    INSERT INTO roles (
                        role_key, name, description, is_system, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [tuple(row) for row in roles],
                )
                connection.executemany(
                    """
                    INSERT INTO role_permissions (role_key, permission, created_at)
                    VALUES (?, ?, ?)
                    """,
                    [tuple(row) for row in permissions],
                )
                connection.commit()

        invalid = self.client.post(
            "/users/save",
            data={
                "username": "invalid-role-user",
                "display_name": "失效角色测试",
                "password": "invalid-role-password",
                "role": "deleted-role",
                "active": "1",
                "permission_selection_present": "1",
            },
        )
        invalid_html = invalid.get_data(as_text=True)
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('<option value="" selected disabled>请选择可用角色</option>', invalid_html)
        admin_option = re.search(r'<option\s+value="admin"(?P<attrs>.*?)>', invalid_html, re.S)
        self.assertIsNotNone(admin_option)
        self.assertNotIn("selected", admin_option.group("attrs"))

    def test_page_templates_keep_only_approved_spacious_homepage_headers(self):
        template_dir = Path(__file__).resolve().parents[1] / "templates"
        page_templates = sorted(template_dir.glob("*.html"))

        self.assertTrue(page_templates)
        for template_path in page_templates:
            with self.subTest(template=template_path.name):
                template = template_path.read_text(encoding="utf-8")
                self.assertNotIn("workspace-header", template)
                self.assertEqual(template.count("search-hero"), 1 if template_path.name == "index.html" else 0)

        materials_template = (template_dir / "materials.html").read_text(encoding="utf-8")
        self.assertIn('class="material-landing"', materials_template)

    def test_navigation_dropdowns_stay_anchored_in_narrow_scrollable_navigation(self):
        precision_css = (Path(__file__).resolve().parents[1] / "static" / "components" / "precision.css").read_text(
            encoding="utf-8"
        )
        nav_js = (Path(__file__).resolve().parents[1] / "static" / "nav.js").read_text(encoding="utf-8")

        self.assertIn(".nav-menu-panel {\n  top: calc(100% - 1px);\n  min-width: 100%;", precision_css)
        self.assertIn("justify-content: center;", precision_css)
        self.assertIn("font-size: 13px;", precision_css)
        self.assertIn("@media (max-width: 760px)", precision_css)
        self.assertIn("position: fixed;\n    top: var(--nav-menu-panel-top, 0px);", precision_css)
        self.assertIn("width: var(--nav-menu-panel-width, auto);", precision_css)
        self.assertIn('window.matchMedia("(max-width: 760px)")', nav_js)
        self.assertIn('panel.style.setProperty("--nav-menu-panel-top"', nav_js)
        self.assertIn('panel.style.setProperty("--nav-menu-panel-left"', nav_js)
        self.assertIn('panel.style.setProperty("--nav-menu-panel-width"', nav_js)
        self.assertIn('addEventListener("scroll", positionMobileMenuPanels', nav_js)

    def test_shared_web_font_is_self_hosted_and_loaded_by_the_page_shell(self):
        root = Path(__file__).resolve().parents[1]
        base_template = (root / "templates" / "base.html").read_text(encoding="utf-8")
        font_css = (root / "static" / "components" / "harmonyos_sans_sc.css").read_text(encoding="utf-8")
        font_directory = root / "static" / "fonts" / "harmonyos-sans-sc"

        self.assertIn("components/harmonyos_sans_sc.css", base_template)
        self.assertNotIn("components/font_faces_00.css", base_template)
        self.assertNotIn("components/font_faces_01.css", base_template)
        self.assertEqual(font_css.count("font-family: 'HarmonyOS Sans SC';"), 3)
        self.assertIn("font-weight: 100 400;", font_css)
        self.assertIn("font-weight: 500 600;", font_css)
        self.assertIn("font-weight: 700 900;", font_css)
        self.assertEqual(
            {path.name for path in font_directory.glob("*.woff2")},
            {
                "HarmonyOS_Sans_SC_Regular.woff2",
                "HarmonyOS_Sans_SC_Medium.woff2",
                "HarmonyOS_Sans_SC_Bold.woff2",
            },
        )
        license_notice = (font_directory / "LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("HarmonyOS Sans Fonts License Agreement", license_notice)
        self.assertIn("prominent notice in the software", license_notice)
        notice = (root / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("HarmonyOS Sans SC fonts", notice)
        self.assertIn("HarmonyOS Sans Fonts License Agreement", notice)

    def test_login_next_rejects_external_url(self):
        response = self.client.post(
            "/login",
            data={"username": "007", "password": "test-admin-pw", "next": "https://example.com/phish"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("example.com", response.headers["Location"])

    def test_download_does_not_send_directories(self):
        self.login()
        output_dir = self.root / "outputs" / "u1-007"
        output_dir.mkdir(parents=True, exist_ok=True)
        response = self.client.get("/download/u1-007", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_core_admin_pages_load(self):
        self.login()
        paths = (
            "/quotes",
            "/contracts",
            "/contracts/sales",
            "/products",
            "/tubes",
            "/materials",
            "/material-drawings",
            "/purchase-contracts",
            "/users",
            "/internal-api-key",
            "/logs",
            "/system-updates",
            "/business-data-sync",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_retired_shipping_pages_and_actions_return_not_found(self):
        self.login()
        requests = (
            ("get", "/shipping-notices"),
            ("post", "/shipping-notices/templates/upload"),
            ("post", "/shipping-notices/templates/batch"),
            ("post", "/shipping-notices/preview"),
            ("post", "/shipping-notices/generate"),
            ("get", "/shipment-recognition"),
            ("post", "/shipment-recognition/run"),
            ("get", "/shipment-recognition/status/retired-job"),
            ("post", "/shipment-recognition/jobs/retired-job/cancel"),
        )
        for method, path in requests:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 404)

    def test_admin_can_generate_and_delete_internal_api_key(self):
        self.login()
        page = self.client.get("/internal-api-key")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("内部 API Key", html)
        self.assertIn("生成 API Key", html)

        no_scope = self.client.post(
            "/internal-api-key/generate",
            data={"name": "No Scope", "scope_selection_present": "1"},
            follow_redirects=True,
        )
        self.assertEqual(no_scope.status_code, 200)
        self.assertIn("API Key 至少需要一个 Scope", no_scope.get_data(as_text=True))

        generated = self.client.post(
            "/internal-api-key/generate",
            data={"name": "OpenClaw Visual"},
        )
        html = generated.get_data(as_text=True)
        self.assertEqual(generated.status_code, 200)
        self.assertEqual(generated.headers.get("Cache-Control"), "no-store")
        token_match = re.search(r'id="generated-api-key">(bld_sk_[^<]+)</code>', html)
        self.assertIsNotNone(token_match)
        token = token_match.group(1)
        self.assertIn("OpenClaw Visual", html)
        self.assertIn("quotes:read", html)
        self.assertIn(token, html)

        generated_second = self.client.post(
            "/internal-api-key/generate",
            data={"name": "OpenClaw Backup"},
        )
        html = generated_second.get_data(as_text=True)
        second_match = re.search(r'id="generated-api-key">(bld_sk_[^<]+)</code>', html)
        self.assertIsNotNone(second_match)
        second_token = second_match.group(1)
        self.assertNotIn(token, html)
        self.assertIn(second_token, html)
        self.assertNotIn("<th>状态</th>", html)
        scope_list = re.search(r'<span class="api-key-scope-list">(.*?)</span>', html, re.DOTALL)
        self.assertIsNotNone(scope_list)
        self.assertIn("读取报价", scope_list.group(1))
        self.assertNotIn("quotes:read", scope_list.group(1))
        self.assertIn("删除", html)
        self.assertIn("交给 AI Agent 的接入约束", html)
        self.assertIn("data-copy-agent-guide", html)
        self.assertIn("新集成不得调用 /api/internal/inquiry/*", html)
        self.assertIn("不得依赖、请求或暴露 output_path、source_path、本机绝对路径", html)

        with self.web.connect(self.web.DB_PATH) as conn:
            first_key = conn.execute(
                "SELECT id FROM internal_api_keys WHERE name = ?",
                ("OpenClaw Visual",),
            ).fetchone()
            key_columns = {row["name"] for row in conn.execute("PRAGMA table_info(internal_api_keys)").fetchall()}
        self.assertIsNotNone(first_key)
        self.assertNotIn("token_plain", key_columns)
        self.assertIn("scopes", key_columns)
        self.assertIn("expires_at", key_columns)

        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-API-VISUAL",
                    "oe_no_1": "API-VISUAL-OE",
                    "active": "1",
                },
                actor="tester",
            )
        api_response = self.client.post(
            "/api/internal/inquiry/analyze",
            json={"numbers": ["API-VISUAL-OE"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(api_response.status_code, 200)
        second_api_response = self.client.post(
            "/api/internal/inquiry/analyze",
            json={"numbers": ["API-VISUAL-OE"]},
            headers={"Authorization": f"Bearer {second_token}"},
        )
        self.assertEqual(second_api_response.status_code, 200)

        deleted = self.client.post("/internal-api-key/delete", data={"key_id": str(first_key["id"])})
        self.assertEqual(deleted.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as conn:
            self.assertIsNone(
                conn.execute("SELECT id FROM internal_api_keys WHERE id = ?", (first_key["id"],)).fetchone()
            )
        rejected = self.client.post(
            "/api/internal/inquiry/analyze",
            json={"numbers": ["NO-MATCH-VISUAL"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(rejected.status_code, 401)
        still_accepted = self.client.post(
            "/api/internal/inquiry/analyze",
            json={"numbers": ["API-VISUAL-OE"]},
            headers={"Authorization": f"Bearer {second_token}"},
        )
        self.assertEqual(still_accepted.status_code, 200)

        delete_all = self.client.post("/internal-api-key/delete", data={})
        self.assertEqual(delete_all.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM internal_api_keys").fetchone()[0], 0)
        all_rejected = self.client.post(
            "/api/internal/inquiry/analyze",
            json={"numbers": ["NO-MATCH-VISUAL"]},
            headers={"Authorization": f"Bearer {second_token}"},
        )
        self.assertEqual(all_rejected.status_code, 401)
