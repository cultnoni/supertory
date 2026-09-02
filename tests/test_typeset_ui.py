"""Frontend contract for the compact typeset toolbar (collapse + grouped fields)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TypesetToolbarUiTests(unittest.TestCase):
    def test_details_toggle_and_grouped_fields(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="viewerTypesetDetailsToggle"', html)
        self.assertIn('id="viewerTypesetDetails"', html)
        self.assertIn("viewer-typeset-presets", html)
        self.assertIn("viewer-typeset-fields", html)
        self.assertIn("viewer-typeset-group", html)
        self.assertIn("viewer-typeset-actions", html)
        self.assertIn("index.상세_설정", html)
        self.assertIn("index.접기", html)
        self.assertLess(
            html.find('id="viewerTypesetDetailsToggle"'),
            html.find('id="viewerTypesetDetails"'),
        )
        self.assertLess(
            html.find('id="viewerTypesetSave"'),
            html.find('id="viewerTypesetExport"'),
        )
        self.assertGreater(
            html.find('id="viewerTypesetDetails"'),
            html.find('id="viewerTypesetPlatforms"'),
        )
        self.assertIn('id="viewerTypesetSave"', html[html.find('id="viewerTypesetDetails"') :])

        self.assertIn('supertory.viewerTypesetDetailsOpen', app_js)
        self.assertIn("function applyTypesetDetailsOpen", app_js)
        self.assertIn("function toggleTypesetDetailsOpen", app_js)

        self.assertIn(".viewer-typeset-details-toggle", css)
        self.assertIn(".viewer-typeset-group + .viewer-typeset-group", css)
        self.assertIn("border-left: 0.5px solid var(--border-strong)", css)
        self.assertIn(".viewer-typeset-field", css)

    def test_numeric_fields_are_selects_with_custom_option(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        fields = (
            "viewerTypesetFontSize",
            "viewerTypesetLineHeight",
            "viewerTypesetLetterSpacing",
            "viewerTypesetIndent",
            "viewerTypesetParaSpacing",
            "viewerTypesetMarginLeft",
            "viewerTypesetMarginRight",
            "viewerTypesetMarginTop",
            "viewerTypesetMarginBottom",
            "viewerTypesetViewport",
        )
        for field_id in fields:
            self.assertIn(f'<select id="{field_id}"', html)
            self.assertIn(f'id="{field_id}Custom"', html)
            self.assertIn(f'value="__custom__"', html)
        self.assertIn('value="9"', html)
        self.assertIn('value="14"', html)
        self.assertIn('value="130"', html)
        self.assertIn('value="180"', html)
        self.assertIn('value="-0.05"', html)
        self.assertIn('value="0.1"', html)
        self.assertIn('value="320"', html)
        self.assertIn('value="414"', html)
        self.assertIn("index.직접입력", html)
        self.assertIn("index.들여쓰기_1칸", html)
        self.assertIn("index.들여쓰기_2칸", html)
        self.assertNotIn('id="viewerTypesetFontSize" type="number"', html)
        self.assertIn("TYPESET_SELECT_CUSTOM", app_js)
        self.assertIn("TYPESET_FIELD_PRESETS", app_js)
        self.assertIn("function setTypesetChoice", app_js)
        self.assertIn("function setTypesetIndentChoice", app_js)
        self.assertIn("function onTypesetChoiceSelect", app_js)
        self.assertIn(".viewer-typeset-custom-input", css)
        self.assertIn(".viewer-typeset-field select", css)

    def test_detail_locale_keys_synced(self) -> None:
        for name in ("ko", "en", "es"):
            data = json.loads((ROOT / "web" / "locales" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertIn("index.상세_설정", data)
            self.assertTrue(str(data["index.상세_설정"]).strip())
            self.assertIn("index.접기", data)
            self.assertEqual(
                str(data["index.직접입력"]).strip() != "",
                True,
            )
            self.assertTrue(str(data["index.들여쓰기_1칸"]).strip())
            self.assertTrue(str(data["index.들여쓰기_2칸"]).strip())


if __name__ == "__main__":
    unittest.main()
