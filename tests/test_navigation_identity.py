import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NavigationIdentityTest(unittest.TestCase):
    def test_navigation_prefers_display_name_and_keeps_login_and_role(self):
        template = (ROOT / "templates/_nav.html").read_text()

        expected = (
            "{{ g.user.display_name or g.user.username }}</strong>"
            "<small>{{ g.user.username }} · {{ g.user.role_name or ROLE_LABELS.get(g.user.role, g.user.role) }}</small>"
        )
        self.assertEqual(template.count(expected), 1)

    def test_user_menu_exposes_change_password_and_gates_admin_links(self):
        template = (ROOT / "templates/_nav.html").read_text()

        self.assertIn('<details class="admin-menu" data-admin-menu>', template)
        self.assertNotIn(
            '{% if can("manage_users") or can("view_logs") or can("sync_product_data") %}',
            template,
        )
        self.assertIn("url_for('change_password')", template)
        self.assertIn("账号设置", template)
        self.assertIn('{% if can("manage_users") %}', template)
        self.assertIn('{% if can("view_logs") %}', template)
        self.assertIn('{% if can("sync_product_data") %}', template)

    def test_mobile_navigation_marks_only_existing_core_entries_and_keeps_permission_guards(self):
        template = (ROOT / "templates/_nav.html").read_text()
        precision_css = (ROOT / "static/components/precision.css").read_text()
        nav_js = (ROOT / "static/nav.js").read_text()

        self.assertIn('class="mobile-workspace-trigger" type="button" aria-expanded="false"', template)
        self.assertIn('aria-controls="mobile-workspace-nav" data-mobile-workspace-trigger', template)
        self.assertIn('class="nav-links" id="mobile-workspace-nav" data-mobile-workspace-menu', template)
        self.assertIn('{% if active_page == \'match\' %}', template)
        self.assertIn('{% if active_page == \'products\' %}', template)
        self.assertIn('{% if can("view_products") %}\n    <a class="nav-mobile-core', template)
        self.assertIn('{% if can("view_customers") %}\n      <a class="nav-mobile-secondary', template)
        self.assertIn('{% if can("view_customer_prices") %}\n      <a class="nav-mobile-secondary', template)
        self.assertIn('{% if can("view_contracts") %}\n      <div class="nav-menu contract-nav-menu nav-mobile-secondary"', template)
        self.assertIn('{% if can("view_material_drawings") %}\n      <a class="nav-mobile-secondary', template)
        self.assertIn("grid-template-columns: 58px minmax(0, 1fr) auto;", precision_css)
        self.assertIn(".app-nav.is-mobile-menu-open .nav-links {\n    display: grid;", precision_css)
        self.assertIn('nav.querySelector("[data-mobile-workspace-trigger]")', nav_js)
        self.assertIn('mobileWorkspaceMenu.addEventListener("click"', nav_js)
        self.assertIn('event.key === "Escape" && nav.classList.contains("is-mobile-menu-open")', nav_js)
