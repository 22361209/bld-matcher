import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminAddFormLayoutTest(unittest.TestCase):
    def test_admin_add_forms_reuse_the_product_search_field_layout(self):
        customer_template = (ROOT / "templates/customers.html").read_text()
        product_options_template = (ROOT / "templates/product_options.html").read_text()

        self.assertIn('class="search-form customer-add-form"', customer_template)
        self.assertIn('class="search-form product-option-add-form"', product_options_template)

    def test_admin_add_forms_keep_the_standard_content_inset(self):
        for stylesheet, selector, button_selector in (
            ("static/pages/customers.css", ".customer-add-form", ".customer-add-form > .linear-button"),
            ("static/pages/product_options.css", ".product-option-add-form", ".product-option-add-form > .linear-button"),
        ):
            content = (ROOT / stylesheet).read_text()
            self.assertIn(f"{selector} {{", content)
            self.assertIn("  margin: 12px 16px;", content)
            self.assertIn("  flex-wrap: nowrap;", content)
            self.assertIn("margin-inline: 12px;", content)
            self.assertIn(f"{button_selector} {{\n  flex: 0 0 auto;\n}}", content)
            self.assertIn("  flex: 0 0 320px;", content)
