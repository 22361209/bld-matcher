from pathlib import Path
import unittest


class ComboboxStyleTests(unittest.TestCase):
    def test_options_use_existing_workspace_color_tokens(self):
        stylesheet = (
            Path(__file__).resolve().parents[1]
            / "static"
            / "components"
            / "combobox.css"
        ).read_text(encoding="utf-8")

        self.assertIn("background: var(--linear-surface);", stylesheet)
        self.assertIn("border: 1px solid var(--linear-line);", stylesheet)
        self.assertIn("color: var(--linear-text);", stylesheet)
        self.assertIn("background: var(--linear-subtle);", stylesheet)
        self.assertIn("color: var(--linear-muted);", stylesheet)
        self.assertIn("color: var(--linear-accent);", stylesheet)

    def test_other_candidate_popovers_keep_an_explicit_readable_text_color(self):
        root = Path(__file__).resolve().parents[1]
        product_picker = (root / "static/pages/products.css").read_text(encoding="utf-8")
        column_filter = (root / "static/pages/product_table.css").read_text(encoding="utf-8")

        self.assertIn(".product-option-picker-dropdown", product_picker)
        self.assertIn("background: var(--linear-surface);", product_picker)
        self.assertIn(".product-option-picker-option", product_picker)
        self.assertIn("color: var(--linear-text);", product_picker)
        self.assertIn("background: var(--linear-subtle);", product_picker)

        self.assertIn(".products-column-filter", column_filter)
        self.assertIn("color: var(--linear-text);", column_filter)
        self.assertIn(".products-column-filter-option", column_filter)
        self.assertIn("background: var(--linear-subtle);", column_filter)
