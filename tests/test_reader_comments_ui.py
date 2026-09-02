"""Frontend contract for virtual-reader comments dock and history toggle."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReaderCommentsUiTests(unittest.TestCase):
    def test_comments_dock_and_history_toggle_markup(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        ko = (ROOT / "web" / "locales" / "ko.json").read_text(encoding="utf-8")

        self.assertIn('id="viewerReaderCommentsPanel"', html)
        self.assertIn("viewer-comments-dock", html)
        self.assertIn('id="viewerReaderCommentsHistoryToggle"', html)
        self.assertIn('id="viewerReaderCommentsHistoryButton"', html)
        self.assertIn('id="viewerReaderCommentsHistoryList"', html)
        self.assertIn("index.접기", html)
        self.assertIn("disabled", html)
        self.assertNotIn(
            'id="viewerReaderCommentsPanel" class="viewer-toc-panel',
            html.replace("\n", " "),
        )

        self.assertIn("function viewerReaderCommentBatches", app_js)
        self.assertIn("function toggleViewerCommentsHistory", app_js)
        self.assertIn("function syncViewerCommentsHistoryToggle", app_js)
        self.assertIn("viewerCommentsHistoryOpen", app_js)
        self.assertIn("startIfNeeded: true", app_js)
        self.assertIn("has-comments-dock", app_js)

        self.assertIn(".viewer-comments-dock", css)
        self.assertIn(".viewer-card.has-comments-dock .viewer-stage", css)

        self.assertIn("이전 댓글 보기", ko)
        self.assertIn("완성되면 <strong>[뷰어]</strong>에서 가상독자 댓글을 확인할 수 있어요", ko)

    def test_icon_guide_tooltip_is_single_active(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="iconGuideTooltip"', html)
        self.assertIn("let activeTooltipId = null", app_js)
        self.assertIn("function closeIconGuidePopovers", app_js)
        self.assertIn("function setActiveTooltip", app_js)
        self.assertIn("function setupIconGuideTooltips", app_js)
        self.assertIn("setupIconGuideTooltips()", app_js)
        self.assertIn(".icon-guide-tooltip", css)
        self.assertNotIn('logBtn.addEventListener("contextmenu"', app_js)


if __name__ == "__main__":
    unittest.main()
