"""Frontend contract for virtual-reader comments in the result pane and history toggle."""

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
        en = (ROOT / "web" / "locales" / "en.json").read_text(encoding="utf-8")
        es = (ROOT / "web" / "locales" / "es.json").read_text(encoding="utf-8")

        self.assertIn('id="viewerReaderCommentsPanel"', html)
        self.assertIn('id="aiResultReaderCommentsButton"', html)
        self.assertIn('id="viewerReaderCommentsStartButton"', html)
        self.assertIn('id="aiResultCommentsDot"', html)
        # 뷰어 안에는 더 이상 댓글 UI가 없다
        self.assertNotIn("viewer-comments-dock", html)
        self.assertNotIn('id="viewerReaderCommentsButton"', html)
        self.assertNotIn('id="viewerModeCommentsBadge"', html)
        self.assertIn('id="viewerReaderCommentsHistoryToggle"', html)
        self.assertIn('id="viewerReaderCommentsHistoryList"', html)
        self.assertIn("disabled", html)

        self.assertIn("function viewerReaderCommentBatches", app_js)
        self.assertIn("function viewerReaderCommentPreviousBatches", app_js)
        self.assertIn("function toggleViewerCommentsHistory", app_js)
        self.assertIn("function syncViewerCommentsHistoryToggle", app_js)
        self.assertIn("persisted.slice(1)", app_js)
        self.assertIn("app.이전_댓글이_없어요", app_js)
        self.assertIn("viewerCommentsHistoryOpen", app_js)
        self.assertIn("startIfNeeded: start", app_js)
        self.assertIn("function openReaderCommentsPane", app_js)
        self.assertIn("function syncReaderCommentsPaneState", app_js)
        self.assertIn("is-comments-view", app_js)
        self.assertIn("data-tory-reader-comments-open", app_js)
        self.assertNotIn("has-comments-dock", app_js)

        self.assertIn(".ai-result-wrap.is-comments-view", css)
        self.assertNotIn(".viewer-comments-dock", css)

        self.assertIn("이전 댓글 보기", ko)
        self.assertIn("이전 댓글이 없어요", ko)
        self.assertIn("No previous comments", en)
        self.assertIn("No hay comentarios anteriores", es)
        self.assertIn("완성되면 <strong>[결과보기]</strong>에서 가상독자 댓글을 확인할 수 있어요", ko)
        for key in (
            "index.가상독자_댓글_보기",
            "app.가상독자_댓글_받기_안내",
            "app.가상독자_댓글_잠김_안내",
            "app.가상독자_댓글_알림_제목",
            "app.가상독자_댓글_알림_본문",
            "app.가상독자_댓글_보러_가기",
        ):
            for locale in (ko, en, es):
                self.assertIn('"%s"' % key, locale)

    def test_opening_comments_panel_only_generates_when_no_batches_exist(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        load_fn = app_js.split("async function loadViewerReaderComments", 1)[1].split(
            "async function refreshViewerReaderComments", 1
        )[0]
        refresh_fn = app_js.split("async function refreshViewerReaderComments", 1)[1].split(
            "function openViewerReaderCommentsFromCta", 1
        )[0]
        self.assertIn("options.startIfNeeded", load_fn)
        self.assertIn("hasPersistedBatch", load_fn)
        self.assertIn("!hasPersistedBatch", load_fn)
        self.assertIn("/reader-comments/generate", load_fn)
        self.assertIn("startIfNeeded: start", app_js)
        self.assertIn("/reader-comments/generate", refresh_fn)
        self.assertNotIn("hasPersistedBatch", refresh_fn)

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
