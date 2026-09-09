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
        actions = self.html.split('class="tory-chat-toolbar-actions"', 1)[1].split(
            'id="toryChatSuccessBanner"', 1
        )[0]
        self.assertLess(
            actions.find('id="toryChatHighlightButton"'),
            actions.find('id="toryChatPopupOpenButton"'),
        )

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
        result_actions = self.html.split('class="ai-result-head-actions"', 1)[1].split("</div>", 1)[0]
        self.assertLess(
            result_actions.find('id="aiResultExpandButton"'),
            result_actions.find('id="aiResultHistoryButton"'),
        )
        self.assertNotIn("요청을_넓게_적어요_아래_버튼으로_바로_토리", self.html)

    def test_ai_result_modal_is_resizable(self) -> None:
        card = self.html.split('id="aiResultModal"', 1)[1].split('id="chapterSubtitleModal"', 1)[0]
        for edge in ("n", "s", "e", "w", "ne", "nw", "se", "sw"):
            self.assertIn(f'data-resize-edge="{edge}"', card)
        self.assertIn('id="aiResultModalDrag"', card)
        self.assertIn("function setupAiResultModalChrome", self.js)
        self.assertIn("supertory.aiResultModalGeom", self.js)
        self.assertIn(".ai-result-modal-card.is-user-sized", self.css)
        self.assertIn("applyFloatingPopupResize(card", self.js)

    def test_tory_notify_has_large_view_popup(self) -> None:
        self.assertIn('id="toryNotifyPopup"', self.html)
        self.assertIn('id="toryNotifyPopupDockHint"', self.html)
        self.assertIn("function openToryNotifyPopup", self.js)
        self.assertIn("function closeToryNotifyPopup", self.js)
        self.assertIn(".tory-notify-expand-btn", self.css)
        self.assertIn(".tory-notify-popup-body", self.css)

    def test_result_history_has_popup_option(self) -> None:
        self.assertNotIn('id="aiPanelHistoryPopupButton"', self.html)
        self.assertIn("data-ai-panel-history-popup", self.js)
        self.assertIn('id="aiResultHistoryPopupButton"', self.html)
        self.assertIn("function popupAiResultHistoryEntry", self.js)
        expand_click = self.js.split('$("aiResultExpandButton")?.addEventListener("click"', 1)[1].split(
            "modal.querySelectorAll", 1
        )[0]
        self.assertIn("isAiPanelHistoryOpen()", expand_click)
        self.assertIn("popupAiResultHistoryEntry", expand_click)
        hide = self.css.split(".ai-result-wrap.is-history-view #aiResultLivePane", 1)[1].split(
            ".ai-result-history-pane {", 1
        )[0]
        self.assertNotIn("#aiResultExpandButton", hide)
        hub_fn = self.js.split("function setupToryChatHubUi(", 1)[1].split(
            "function setupToryChatPopupChrome(", 1
        )[0]
        self.assertNotIn('openDockFloat("toryChat")', hub_fn)
        self.assertNotIn('openDockFloat("characterChat")', hub_fn)
        self.assertNotIn('openDockFloat("readerChat")', hub_fn)

    def test_tory_chat_history_is_in_panel_except_popup(self) -> None:
        self.assertIn('id="toryChatHistoryPane"', self.html)
        self.assertIn("function setToryChatPanelHistoryOpen", self.js)
        click_fn = self.js.split("function onToryChatHistoryButtonClick", 1)[1].split(
            "function ", 1
        )[0]
        self.assertIn("toryChatPopupOpen", click_fn)
        self.assertIn("openToryChatHistoryModal", click_fn)
        hide = self.css.split(".tory-chat-popup .tory-chat-history-pane", 1)[1].split("}", 1)[0]
        self.assertIn("display: none !important", hide)


if __name__ == "__main__":
    unittest.main()
