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
        self.assertIn("修改密码", template)
        self.assertIn('{% if can("manage_users") %}', template)
        self.assertIn('{% if can("view_logs") %}', template)
        self.assertIn('{% if can("sync_product_data") %}', template)
