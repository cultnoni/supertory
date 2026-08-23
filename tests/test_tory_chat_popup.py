"""1:1 토리 대화: 패널·팝업이 같은 입력칸을 쓰고, 형광펜이 패널에도 남는지."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ToryChatPopupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    def test_single_chat_composer(self) -> None:
        self.assertEqual(self.html.count('id="toryChatInput"'), 1)
        self.assertIn('id="toryChatPopupBody"', self.html)
        self.assertNotIn('id="toryChatPopupInput"', self.html)

    def test_popup_preserves_composer_draft(self) -> None:
        self.assertIn("function captureToryChatComposerDraft", self.js)
        self.assertIn("function restoreToryChatComposerDraft", self.js)
        open_fn = self.js.split("function openToryChatPopup", 1)[1].split("function askToryFromSelection", 1)[0]
        self.assertIn("captureToryChatComposerDraft", open_fn)
        self.assertIn("restoreToryChatComposerDraft", open_fn)
        self.assertNotIn('setAiPanelTab("tools"', open_fn)

    def test_panel_placeholder_while_popup_open(self) -> None:
        self.assertIn('id="toryChatPopupDockHint"', self.html)
        self.assertIn('id="toryChatPopupDockButton"', self.html)
        self.assertIn("tory-chat-popup-dock-hint", self.css)

    def test_highlighter_stays_in_panel_toolbar(self) -> None:
        self.assertIn('id="toryChatHighlightButton"', self.html)
        self.assertIn("feature-hide-exempt", self.html)
        self.assertIn(".tory-chat-toolbar-actions", self.css)
        self.assertIn("flex-wrap: wrap", self.css)
        hide = self.js.split("function isFeatureHideExempt", 1)[1].split("function isManuscriptWritingSurface", 1)[0]
        self.assertIn("toryChatHighlightButton", hide)
