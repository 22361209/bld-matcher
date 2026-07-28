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
