"""첨삭 피드백 화면 로직(순수 함수)과 문단 파서 대조."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from feedback_pipeline.paragraphs import paragraphs_from_html

ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "test_feedback_panel.js"
FIXTURES = ROOT / "tests" / "fixtures" / "feedback_paragraphs.json"
PANEL_JS = ROOT / "web" / "feedback_panel.js"
HTML = ROOT / "web" / "index.html"


class FeedbackPanelLogicTests(unittest.TestCase):
    def test_node_panel_logic(self) -> None:
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

    def test_js_html_parser_matches_python_fixtures(self) -> None:
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        html_cases = [case for case in cases if case.get("fn") == "html"]
        self.assertTrue(html_cases)
        for case in html_cases:
            with self.subTest(case["id"]):
                self.assertEqual(paragraphs_from_html(case["input"]), case["expected"])

    def test_panel_wired_in_shell(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-analyze-action="feedback"', html)
        self.assertIn("첨삭 피드백", html)
        self.assertNotIn("첨삭 피드백 (새 방식)", html)
        self.assertIn('id="feedbackPanel"', html)
        self.assertIn("/feedback_panel.js", html)
        self.assertIn("dock-float-feedback", js)
        self.assertTrue(PANEL_JS.is_file())
        self.assertIn('data-role="fb-underline"', html)
        self.assertIn("원고에 밑줄 표시", html)
        self.assertIn('id="feedbackReviewHost"', html)
        self.assertNotIn('data-role="fb-switch-chrome"', html)
        self.assertIn("작은 창으로 보기", js)
        self.assertIn("넓게 보기", js)
        self.assertIn('data-role="fb-tab"', html)
        self.assertIn("첨삭 제안", html)
        self.assertIn("openFeedbackChrome", js)
        self.assertIn("fb-review-open", js)
        self.assertIn("시험 모드로 실행돼요", html)
        self.assertIn('data-role="fb-next-fake"', html)
        self.assertIn("새 분석", html)
        self.assertIn("분석 강도", html)
        self.assertIn('data-role="fb-lens"', html)
        self.assertIn("자세히", html)
        self.assertIn('data-role="fb-sort"', html)
        self.assertIn("중요도순", html)
        self.assertIn("원고순", html)
        self.assertIn('data-role="fb-filter-menu"', html)
        self.assertIn('data-role="fb-filter-pop"', html)
        self.assertIn('data-role="fb-display-menu"', html)
        self.assertIn('data-role="fb-display-pop"', html)
        self.assertIn('data-role="fb-kind-list"', html)
        self.assertIn("초기화", html)
        self.assertIn('data-role="fb-filter-banner"', html)
        self.assertIn("원고 표시 설정", html)
        self.assertIn("원고에 밑줄 표시", html)
        self.assertIn("밑줄 표시", html)
        self.assertIn('data-role="fb-start-pop"', html)
        self.assertNotIn('data-role="fb-view-menu"', html)
        self.assertNotIn('data-role="fb-view-pop"', html)
        self.assertNotIn('data-role="fb-kind-chips"', html)
        self.assertNotIn('data-role="fb-ul-menu"', html)
        self.assertNotIn('data-role="fb-run-status"', html)
        start = html.find('id="feedbackPanel"')
        end = html.find('id="analyzeMenuHome"')
        panel_html = html[start:end]
        self.assertNotIn('<input type="radio"', panel_html)
        self.assertNotIn('<input type="checkbox"', panel_html)
        self.assertIn("class=\"fb-toggle\"", panel_html)
        self.assertIn("class=\"fb-seg\"", panel_html)
        self.assertIn("fb-kind-chips", panel_html)
        self.assertIn('data-role="fb-debug-locate"', html)
        self.assertIn("위치를 찾을 수 없어요", PANEL_JS.read_text(encoding="utf-8"))
        js_panel = PANEL_JS.read_text(encoding="utf-8")
        self.assertIn("viewOptionsState", js_panel)
        self.assertIn("controlDisplayState", js_panel)
        self.assertIn("formatFilterBanner", js_panel)
        self.assertIn("underlinePaintTargets", js_panel)
        self.assertNotIn("전체 보기", panel_html)
        self.assertIn('data-role="fb-ul-level"', html)
        self.assertIn("중요만", html)
        self.assertIn("중요+보통", html)
        self.assertIn("fb-legend", html)
        self.assertIn("가나다", html)
        self.assertIn("이 글이 바뀌어요", html)
        self.assertIn("확인할 위치예요", html)
        self.assertIn("사전 표시 숨김", html)
        self.assertIn("검토하는 동안 이름·용어 표시를 잠시 숨겨요.", html)
        self.assertIn('data-role="fb-auto-advance"', html)
        self.assertIn("적용 후 다음 카드로 자동 이동", html)
        self.assertIn("적용된 위치를 찾을 수 없어요. 원고가 그 뒤에 바뀐 것 같아요", js_panel)
        self.assertIn('data-tab="history"', html)
        self.assertIn('data-role="fb-import-legacy"', html)
        self.assertIn("가져올 예전 기록이 없어요", html)
        self.assertIn("예전 피드백 기록 가져오기", PANEL_JS.read_text(encoding="utf-8"))
        self.assertIn("수집에 저장", html)
        self.assertIn("pickDefaultRun", js_panel)
        self.assertIn("nextRunAfterDelete", js_panel)
        self.assertIn("formatLegacyImportMessage", js_panel)
        self.assertIn("<del>가나다</del>", html)
        self.assertIn("이 글이 바뀌어요(수정안 있음)", html)
        self.assertIn('data-role="fb-hide-dict"', html)
        self.assertIn("고치기", PANEL_JS.read_text(encoding="utf-8"))
        self.assertIn("planCardApply", PANEL_JS.read_text(encoding="utf-8"))
        self.assertIn("fbInlineBox", PANEL_JS.read_text(encoding="utf-8"))
        self.assertIn("fbActionBar", PANEL_JS.read_text(encoding="utf-8"))
        self.assertIn("execCommand", PANEL_JS.read_text(encoding="utf-8"))
        self.assertNotIn("insertTextIntoSceneEditor", PANEL_JS.read_text(encoding="utf-8"))
        self.assertNotIn("insertHtmlAtManuscriptContext", PANEL_JS.read_text(encoding="utf-8"))
        self.assertTrue((ROOT / "web" / "dev" / "feedback_header_check.html").is_file())
        self.assertTrue((ROOT / "web" / "dev" / "feedback_view_pops.html").is_file())
        self.assertTrue((ROOT / "web" / "dev" / "feedback_apply_check.html").is_file())

    def test_review_mode_does_not_shrink_manuscript_column(self) -> None:
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        js = PANEL_JS.read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "feedback_api.md").read_text(encoding="utf-8")
        self.assertNotIn(
            "body.fb-review-open .scene-workspace .writing-column",
            css,
        )
        self.assertNotIn("--fb-review-workbar", css)
        self.assertIn("오버레이", css)
        self.assertIn("padding-bottom", js)
        self.assertIn("오버레이", docs)

    def test_priority_highlight_styles(self) -> None:
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("::highlight(fb-high)", css)
        self.assertIn("::highlight(fb-medium)", css)
        self.assertIn("::highlight(fb-low)", css)
        self.assertIn("::highlight(fb-active-note)", css)
        self.assertIn("::highlight(fb-active-mid)", css)
        self.assertIn("background-color: rgba(196, 181, 80, 0.12)", css)
        self.assertIn("background-color: rgba(220, 80, 60, 0.10)", css)
        self.assertIn(".fb-nl-del", css)
        self.assertIn("::highlight(fb-del)", css)
        self.assertIn("::highlight(fb-active-range)", css)
        self.assertIn("text-decoration-style: wavy", css)
        self.assertIn("text-decoration-style: dotted", css)
        self.assertIn("background-color: transparent", css)
        self.assertIn("line-through", css)

    def test_tory_and_toolbar_entry_points_share_open_feedback_chrome(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        panel = PANEL_JS.read_text(encoding="utf-8")
        start = html.find('data-i18n-label="index.토리와_구상해요"')
        end = html.find('data-i18n-label="index.토리와_함께_써요"')
        group = html[start:end]
        self.assertIn('value="analyze"', group)
        self.assertIn("일반 피드백", group)
        self.assertIn('value="markupfeedback"', group)
        self.assertIn("첨삭 피드백", group)
        self.assertLess(group.find('value="analyze"'), group.find('value="markupfeedback"'))
        self.assertIn("function isMarkupFeedbackMode", js)
        self.assertIn("function openMarkupFeedbackChrome", js)
        self.assertIn("function openWidePanel", js)
        self.assertIn("openWidePanel(markupWidePanelSpec(sourceEl || $(\"aiModePicker\")", js)
        self.assertIn('if (action === "feedback")', js)
        self.assertIn("openFeedbackChrome($(\"analyzeMenuButton\"))", js)
        self.assertIn("openMarkupFeedbackChrome($(\"aiModePicker\"))", js)
        locales = {}
        for name in ("ko", "en", "es"):
            locales[name] = json.loads((ROOT / "web" / "locales" / f"{name}.json").read_text(encoding="utf-8"))
        for name, data in locales.items():
            self.assertEqual(data["app.피드백_요청"].strip() != "", True, name)
            self.assertIn("index.첨삭_피드백", data)
            self.assertTrue(str(data["index.첨삭_피드백"]).strip(), name)
            self.assertIn("index.문단을_나눠_리포트와_첨삭_카드를_만들어요", data)
        self.assertEqual(locales["ko"]["app.피드백_요청"], "일반 피드백")
        self.assertEqual(locales["ko"]["index.첨삭_피드백"], "첨삭 피드백")
        self.assertEqual(set(locales["ko"]) & {"index.첨삭_피드백", "index.문단을_나눠_리포트와_첨삭_카드를_만들어요"},
                         set(locales["en"]) & {"index.첨삭_피드백", "index.문단을_나눠_리포트와_첨삭_카드를_만들어요"})
        self.assertEqual(set(locales["ko"]) & {"index.첨삭_피드백", "index.문단을_나눠_리포트와_첨삭_카드를_만들어요"},
                         set(locales["es"]) & {"index.첨삭_피드백", "index.문단을_나눠_리포트와_첨삭_카드를_만들어요"})
        self.assertIn("function legacyImportButtonState", panel)
        self.assertIn("paintLegacyImportButton", panel)
        self.assertIn(".fb-hist-import .compact-btn:disabled", (ROOT / "web" / "styles.css").read_text(encoding="utf-8"))

    def test_tory_history_includes_markup_and_analyze_menu_order(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        panel = PANEL_JS.read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        start = html.find('id="analyzeMenuDropdown"')
        end = html.find('id="writingLogModal"')
        menu = html[start:end]
        focus = menu.find('data-analyze-action="focus"')
        feedback = menu.find('data-analyze-action="feedback"')
        world = menu.find('data-analyze-action="worldscan"')
        dup = menu.find('data-analyze-action="dupcheck"')
        self.assertTrue(0 <= focus < feedback < world < dup, menu[focus:dup + 40])
        picker_start = html.find('data-i18n-label="index.토리와_구상해요"')
        picker_end = html.find('data-i18n-label="index.토리와_함께_써요"')
        group = html[picker_start:picker_end]
        self.assertLess(group.find('value="analyze"'), group.find('value="markupfeedback"'))
        self.assertIn("function mergeToryHistoryItems", panel)
        self.assertIn("function openHistoryRun", panel)
        self.assertIn("function toryHistoryItemFromRun", panel)
        self.assertIn("pendingOpenRunId", panel)
        self.assertIn("function combinedAiResultHistoryItems", js)
        self.assertIn("function openMarkupFeedbackFromHistory", js)
        self.assertIn("FeedbackPanel.openHistoryRun(runId)", js)
        self.assertIn("openFeedbackChrome($(\"aiResultHistoryButton\")", js)
        self.assertIn('data-history-kind="markupfeedback"', js)
        self.assertIn("/api/projects/${pid}/feedback/runs", js)
        self.assertIn("is-markupfeedback", css)
        self.assertIn("AI_RESULT_HISTORY_MAX", js)

    def test_card_comment_ui_is_wired(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        js = PANEL_JS.read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("질문하기", js)
        self.assertIn("이 지적에 대해 궁금한 점이나 다른 의견을 물어보세요", js)
        self.assertIn("답을 받지 못했어요", js)
        self.assertIn("다시 시도", js)
        self.assertIn("fb-comment-toggle", js)
        self.assertIn("fb-comment-send", js)
        self.assertIn("/api/feedback/cards/", js)
        self.assertIn("is-user", js)
        self.assertIn("is-ai", js)
        self.assertIn("is-ignored", js)
        self.assertIn("comment_count", js)
        self.assertIn(".fb-talk-msg.is-user", css)
        self.assertIn("feedback_panel.js?v=24", html)

    def test_locale_keys_for_feedback_names_stay_in_sync(self) -> None:
        keys = (
            "app.피드백_요청",
            "index.첨삭_피드백",
            "index.문단을_나눠_리포트와_첨삭_카드를_만들어요",
            "index.예전_피드백_기록_가져오기",
            "index.가져올_예전_기록이_없어요",
            "index.질문하기",
            "index.질문",
            "index.이_지적에_대해_궁금한_점이나_다른_의견을_물",
        )
        rows = []
        for name in ("ko", "en", "es"):
            data = json.loads((ROOT / "web" / "locales" / f"{name}.json").read_text(encoding="utf-8"))
            rows.append(set(keys))
            for key in keys:
                self.assertIn(key, data, f"{name}.json missing {key}")
                self.assertTrue(str(data[key]).strip(), f"{name}.json empty {key}")
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(rows[1], rows[2])


if __name__ == "__main__":
    unittest.main()
