"""Smart punctuation prefs, pair catalog, and admin UI contracts."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "index.html"
APP_JS = ROOT / "web" / "app.js"
LOGIC = ROOT / "web" / "smart-punctuation.js"
NODE_TEST = ROOT / "tests" / "test_smart_punctuation.js"
LOCALES = [
    ROOT / "web" / "locales" / "ko.json",
    ROOT / "web" / "locales" / "en.json",
    ROOT / "web" / "locales" / "es.json",
]


class SmartPunctuationTests(unittest.TestCase):
    def test_node_pair_logic(self) -> None:
        result = subprocess.run(
            ["node", str(NODE_TEST)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + "\n" + result.stderr)

    def test_admin_panel_not_context_menu(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        app_js = APP_JS.read_text(encoding="utf-8")
        logic = LOGIC.read_text(encoding="utf-8")
        self.assertNotIn('data-context-action="toggle-smart-punctuation"', html)
        self.assertNotIn("smartPunctuationMenuItem", html)
        self.assertIn('id="smartPunctAdminBox"', html)
        self.assertIn('id="smartPunctQuotes"', html)
        self.assertIn('data-smart-punct="paren"', html)
        self.assertIn('data-smart-punct="smartDouble"', html)
        self.assertIn("supertory.smartPunctuation", logic)
        self.assertIn("supertory.smartPunctuation.pairs", logic)
        self.assertIn("function setupSmartPunctuation", app_js)
        self.assertIn("/smart-punctuation.js", html)
        self.assertIn('data-admin-panel="settings"', html)

    def test_locale_keys(self) -> None:
        keys = [
            "index.문장부호_자동완성",
            "index.문장부호_자동완성_설명",
            "index.문장부호_직선_따옴표",
            "index.문장부호_소괄호",
            "index.문장부호_대괄호",
            "index.문장부호_낫표",
            "index.문장부호_겹낫표",
            "index.문장부호_겹화살괄호",
            "index.문장부호_홑화살괄호",
            "index.문장부호_스마트_작은따옴표",
            "index.문장부호_스마트_큰따옴표",
        ]
        for path in LOCALES:
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in keys:
                self.assertIn(key, data, msg=f"{path.name} missing {key}")


if __name__ == "__main__":
    unittest.main()
