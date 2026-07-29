import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminAddFormLayoutTest(unittest.TestCase):
    def test_admin_add_forms_use_the_shared_inline_command(self):
        customer_template = (ROOT / "templates/customers.html").read_text()
        product_options_template = (ROOT / "templates/product_options.html").read_text()

        self.assertIn('data-open-customer-create-modal', customer_template)
        self.assertIn('id="customer-create-dialog"', customer_template)
        self.assertNotIn('placeholder="新增客户名称，例如 宁波多迦"', customer_template)
        self.assertIn(
            'class="search-form inline-search-command product-option-add-form"',
            product_options_template,
        )

    def test_shared_command_keeps_the_input_and_button_together(self):
        content = (ROOT / "static/components/inline_search_command.css").read_text()

        self.assertIn("--search-content-inset: 16px;", content)
        self.assertIn("flex-wrap: nowrap;", content)
        self.assertIn("margin: 12px var(--search-content-inset);", content)
        self.assertIn("flex: 0 0 var(--search-field-width);", content)
        self.assertIn(".inline-search-command > .linear-button", content)
        self.assertIn("flex: 0 0 auto;", content)
        self.assertIn("flex-basis: 100%;", content)
        self.assertIn("margin-inline: 12px;", content)

    def test_admin_pages_do_not_reimplement_the_shared_command_layout(self):
        for stylesheet, selector in (
            ("static/pages/product_options.css", ".product-option-add-form"),
        ):
            self.assertNotIn(selector, (ROOT / stylesheet).read_text())
