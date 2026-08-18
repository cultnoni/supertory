"""Frontend contract smoke for virtual-reader debate chat room."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReaderDebateUiTests(unittest.TestCase):
    def test_debate_room_markup_and_handlers(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="readerDebatePeers"', html)
        self.assertIn('id="readerDebateMessages"', html)
        self.assertIn('id="readerDebateForm"', html)
        self.assertIn('id="readerDebateInput"', html)
        self.assertIn('id="readerDebateAttachButton"', html)
        self.assertNotIn("토론 진행 화면은 곧 연결돼요", html)

        self.assertIn("async function openReaderDebateRoomAsync", app_js)
        self.assertIn("async function sendReaderDebateMessage", app_js)
        self.assertIn("async function playReaderDebateReplies", app_js)
        self.assertIn("function skipReaderDebateReveal", app_js)
        self.assertIn('"/api/reader-debate"', app_js)
        self.assertIn("/api/reader-debate/history?", app_js)
        self.assertIn("readerManuscriptAttachTarget === \"debate\"", app_js)
        # 1:1 chat path still present
        self.assertIn('"/api/reader-chat"', app_js)
        self.assertIn("async function sendReaderChatMessage", app_js)

        self.assertIn(".reader-debate-peers", css)
        self.assertIn(".reader-debate-round-sep", css)


if __name__ == "__main__":
    unittest.main()
