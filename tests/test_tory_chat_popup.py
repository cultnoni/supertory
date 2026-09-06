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

    def test_expand_and_history_icons_are_unified(self) -> None:
        expand = 'rect x="3" y="5" width="14" height="11" rx="1.5"'
        footprints = "M4 16v-2.38C4 11.5 2.97 10.5 3 8c.03-2.72 1.49-6 4.5-6"
        prompt = self.html.split('id="aiPromptExpandButton"', 1)[1].split("</button>", 1)[0]
        result_expand = self.html.split('id="aiResultExpandButton"', 1)[1].split("</button>", 1)[0]
        chat_expand = self.html.split('id="toryChatPopupOpenButton"', 1)[1].split("</button>", 1)[0]
        notify_expand = self.html.split('id="toryNotifyPopupOpenButton"', 1)[1].split("</button>", 1)[0]
        for chunk in (prompt, result_expand, chat_expand, notify_expand):
            self.assertIn(expand, chunk)
        chat_history = self.html.split('id="toryChatHistoryButton"', 1)[1].split("</button>", 1)[0]
        result_history = self.html.split('id="aiResultHistoryButton"', 1)[1].split("</button>", 1)[0]
        self.assertIn(footprints, chat_history)
        self.assertIn(footprints, result_history)
        self.assertNotIn("M3 12a9 9 0 1 0 3-6.7", chat_history)
        self.assertNotIn(">히스토리<", result_history)
        self.assertNotIn(">크게보기<", result_expand)
        self.assertNotIn(">크게보기<", prompt)

    def test_tory_notify_has_large_view_popup(self) -> None:
        self.assertIn('id="toryNotifyPopup"', self.html)
        self.assertIn('id="toryNotifyPopupDockHint"', self.html)
        self.assertIn("function openToryNotifyPopup", self.js)
        self.assertIn("function closeToryNotifyPopup", self.js)
        self.assertIn(".tory-notify-expand-btn", self.css)
        self.assertIn(".tory-notify-popup-body", self.css)
