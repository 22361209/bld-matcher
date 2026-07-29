import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NavigationIdentityTest(unittest.TestCase):
    def test_navigation_prefers_display_name_and_keeps_login_and_role(self):
        template = (ROOT / "templates/_nav.html").read_text()

        expected = (
            "{{ g.user.display_name or g.user.username }}</strong>"
            "<small>{{ g.user.username }} · {{ ROLE_LABELS.get(g.user.role, g.user.role) }}</small>"
        )
        self.assertEqual(template.count(expected), 2)
