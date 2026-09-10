"""분할 기본 보기 방식: 설정·우클릭 메뉴·자동 적용이 연결돼 있는지."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SplitDefaultModeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.ko = json.loads((ROOT / "web" / "locales" / "ko.json").read_text(encoding="utf-8"))
        cls.en = json.loads((ROOT / "web" / "locales" / "en.json").read_text(encoding="utf-8"))
        cls.es = json.loads((ROOT / "web" / "locales" / "es.json").read_text(encoding="utf-8"))

    def test_admin_settings_has_no_default_view_mode_block(self) -> None:
        self.assertNotIn('id="adminSplitDefaultSection"', self.html)
        self.assertNotIn('name="adminSplitDefaultMode"', self.html)
        self.assertNotIn("function syncAdminSplitDefaultRadios", self.js)
        settings_html = self.html.split('data-admin-panel="settings"', 1)[1].split(
            'data-admin-panel="account"', 1
        )[0]
        self.assertNotIn("index.매번_고르기", settings_html)
        self.assertNotIn("index.분할_버튼을_눌렀을_때_바로_적용할_방식을", settings_html)

    def test_context_menu_change_and_clear(self) -> None:
        self.assertIn('id="splitDefaultChangeMenu"', self.html)
        self.assertIn("index.기본값_변경", self.html)
        self.assertIn("index.기본값_해제", self.js)
        self.assertIn("splitDefaultContextExtras", self.js)
        extras = self.js.split("function splitDefaultContextExtras", 1)[1].split(
            "function isSplitDefaultControl", 1
        )[0]
        self.assertIn("index.기본값_변경", extras)
        self.assertIn("index.기본값_해제", extras)

    def test_split_click_applies_saved_default(self) -> None:
        self.assertIn("function applySplitDefaultOrOpenMenu", self.js)
        self.assertIn("getSplitDefaultMode()", self.js)
        self.assertIn("supertory.splitDefaultMode", self.js)
        apply_fn = self.js.split("function applySplitDefaultOrOpenMenu", 1)[1].split(
            "function loadSplitEditPreference", 1
        )[0]
        self.assertIn("handleViewModeChoice(def)", apply_fn)
        self.assertIn("closeSplitView()", apply_fn)
        self.assertIn("toggleViewModeMenu", apply_fn)
        self.assertIn('applySplitDefaultOrOpenMenu("main")', self.js)
        self.assertIn('applySplitDefaultOrOpenMenu("focus")', self.js)
        chrome = self.js.split("function updateSplitChrome", 1)[1].split(
            "function clearSplitViewerPopupStyles", 1
        )[0]
        self.assertNotIn("index.켜기", chrome)
        self.assertNotIn("index.끄기", chrome)
        self.assertIn("index.분할", chrome)
        self.assertIn("app.분할", chrome)
        self.assertIn("app.팝업_중", chrome)
        self.assertIn("app.화면_나누기_중", chrome)
        self.assertIn("splitDefaultButtonTitle()", chrome)
        self.assertIn("[data-split-chrome='single']", chrome)
        focus_btn = self.html.split('id="focusWriteSplitButton"', 1)[1].split("</button>", 1)[0]
        self.assertIn('data-split-chrome="split"', focus_btn)
        self.assertIn('data-split-chrome="single"', focus_btn)
        self.assertIn('rect x="3.5" y="4.5" width="7" height="15"', focus_btn)
        self.assertIn('<rect width="18" height="18" x="3" y="3" rx="2"/>', focus_btn)
        self.assertNotIn("data-i18n=\"app.분할\"", focus_btn)
        main_btn = self.html.split('id="splitViewButton"', 1)[1].split("</button>", 1)[0]
        self.assertIn('data-split-chrome="single"', main_btn)
        self.assertIn('<rect width="18" height="18" x="3" y="3" rx="2"/>', main_btn)

    def test_split_menu_has_compact_default_and_hint_options(self) -> None:
        self.assertIn('data-split-default-set="split"', self.html)
        self.assertIn('data-split-default-set="popup"', self.html)
        self.assertIn('data-split-default-set="clear"', self.html)
        self.assertIn('data-split-rightclick-hint="hide"', self.html)
        self.assertIn("view-mode-dropdown-mini", self.html)
        self.assertIn("function syncSplitModeMenuExtras", self.js)
        self.assertIn("portalSplitModeDropdownToBody", self.js)
        self.assertIn("supertory.splitRightClickHintHidden", self.js)

    def test_locale_keys_exist(self) -> None:
        keys = (
            "index.기본_보기_방식",
            "index.기본값_변경",
            "index.기본값_해제",
            "index.기본값_화면_나누기",
            "index.기본값_팝업",
            "index.해제",
            "index.우클릭_안내_숨기기",
            "index.우클릭_안내를_숨겼어요",
            "index.기본_보기_방식을_화면_나누기로_저장했어요",
            "index.기본_보기_방식을_팝업으로_저장했어요",
            "index.기본_보기_방식을_해제했어요",
        )
        for key in keys:
            for locale in (self.ko, self.en, self.es):
                self.assertIn(key, locale)
                self.assertTrue(str(locale[key]).strip())

    def test_split_overlays_sit_above_resizer(self) -> None:
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        resizer = css.split(".split-pane-resizer {", 1)[1].split(".split-pane-resizer-hit", 1)[0]
        self.assertIn("z-index: 5;", resizer)
        self.assertNotIn("z-index: 200;", resizer)
        self.assertNotIn("isolation: isolate", resizer)
        self.assertIn("#uiFeatureContextMenu", css)
        self.assertIn("z-index: 250;", css.split("#uiFeatureContextMenu", 1)[1][:80])
        palette = css.split(".format-color-palette {", 1)[1][:80]
        self.assertIn("z-index: 250;", palette)

    def test_split_chrome_slides_down_on_hover(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="split-head-hotzone"', html)
        self.assertIn('class="split-head-chrome"', html)
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        chrome = css.split(
            ".scene-workspace.split-active #splitViewer:not(.popup-mode) .split-head-chrome,",
            1,
        )[1].split(
            ".scene-workspace.split-active #splitViewer:not(.popup-mode):has(.split-head-hotzone:hover)",
            1,
        )[0]
        self.assertIn("transform: translateY(-100%)", chrome)
        self.assertIn("opacity: 0", chrome)
        revealed = css.split(
            ".scene-workspace.split-active #splitViewer:not(.popup-mode):has(.split-head-hotzone:hover) .split-head-chrome,",
            1,
        )[1].split("@media (prefers-reduced-motion: reduce)", 1)[0]
        self.assertIn("transform: translateY(0)", revealed)
        self.assertIn("#switchSplitModeButton[aria-expanded=\"true\"]", revealed)

    def test_split_layout_keeps_side_panel_gutters(self) -> None:
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        workspace = css.split(".scene-workspace.split-active {", 1)[1].split("}", 1)[0]
        self.assertIn("padding: 0;", workspace)
        self.assertNotIn("padding: 8px 8px 0", workspace)
        viewer = css.split(
            ".scene-workspace.split-active #splitViewer:not(.popup-mode) {\n"
            "  flex: 1 1 0;",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("padding: 0 var(--ms-h-gutter", viewer)
        self.assertIn("padding: 0 var(--ms-h-gutter, calc(8px - 1.5mm)) 10px 0", viewer)
        self.assertIn("border: 0", viewer)
        self.assertNotIn("padding: 8px 8px 8px 4px", viewer)
        resizer = css.split(".split-pane-resizer {", 1)[1].split(".split-pane-resizer-hit", 1)[0]
        self.assertIn("flex: 0 0 6px", resizer)
        self.assertNotIn("flex: 0 0 12px", resizer)
        content = css.split(
            ".scene-workspace.split-active #splitViewer:not(.popup-mode) .split-scene-content {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("padding: 0 !important", content)
        self.assertIn("border: 1px solid var(--page-line, var(--line))", content)
        self.assertIn("border-radius: 10px !important", content)
        self.assertNotIn("border: 0 !important", content)
        self.assertNotIn("border-radius: 0 !important", content)

    def test_split_manuscript_page_leaves_room_for_status_bar(self) -> None:
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        page = css.split(
            ".scene-workspace.split-active .manuscript-page {", 1
        )[1].split("}", 1)[0]
        self.assertIn("flex: 1 1 auto !important", page)
        self.assertIn("min-height: 0 !important", page)
        self.assertIn("overflow: auto !important", page)
        self.assertNotIn("height: 100%", page)
        self.assertNotIn("min-height: 180px", page)

    def test_focus_write_hint_is_dismissible(self) -> None:
        self.assertIn('data-guide-tip="focusWriteHint"', self.html)
        self.assertIn('data-guide-tip-dismiss="focusWriteHint"', self.html)
        self.assertIn('class="guide-tip-box focus-write-hint-box"', self.html)
        self.assertIn('{ id: "focusWriteHint"', self.js)
        for locale in (self.ko, self.en, self.es):
            self.assertIn("app.큰_창_안내", locale)
            self.assertTrue(str(locale["app.큰_창_안내"]).strip())
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(
            '.focus-write-modal:not(.is-fullscreen) .focus-write-head:has([data-guide-tip="focusWriteHint"].hidden)',
            css,
        )
        shrink = css.split(
            ".focus-write-modal:not(.is-fullscreen) .focus-write-head:has([data-guide-tip=\"focusWriteHint\"].hidden) {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("align-items: center", shrink)
        self.assertIn("padding: 8px 16px", shrink)
        self.assertIn(".focus-write-modal.is-fullscreen .focus-write-hint-box", css)

    def test_focus_write_split_fills_like_manuscript(self) -> None:
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        slot = css.split("\n.focus-write-split-slot {", 1)[1].split("}", 1)[0]
        self.assertIn("background: var(--page-bg", slot)
        self.assertNotIn("background: var(--header-bg", slot)
        viewer = css.split(
            ".focus-write-split-slot > #splitViewer {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("padding: 0 !important", viewer)
        self.assertIn("background: transparent !important", viewer)
        content = css.split(
            ".focus-write-card.is-split #splitViewer:not(.popup-mode) .split-scene-content {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("padding: 0 !important", content)
        self.assertIn("border: 0 !important", content)
        self.assertIn("border-radius: 0 !important", content)
        self.assertNotIn("background: var(--header-bg", content)

    def test_fullscreen_split_header_is_not_covered_by_focus_head(self) -> None:
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(
            ".focus-write-modal.is-fullscreen .focus-write-card.is-split .focus-write-head-hotzone",
            css,
        )
        constrained = css.split(
            ".focus-write-modal.is-fullscreen .focus-write-card.is-split .focus-write-head-hotzone,",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("right: calc(var(--focus-split-right-width", constrained)
        self.assertIn("+ 6px)", constrained)
        slot = css.split(
            ".focus-write-modal.is-fullscreen .focus-write-card.is-split .focus-write-split-slot {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("z-index: 21", slot)

    def test_focus_write_sheet_looks_like_a4_on_desk(self) -> None:
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('class="focus-write-sheet"', self.html)
        page = css.split("\n.focus-write-page {", 1)[1].split("}", 1)[0]
        self.assertIn("background: var(--page-editor-bg, var(--page-bg, #fffdf8))", page)
        self.assertNotIn("background: #e8e8e8", page)
        self.assertIn("minmax(100%, max-content)", page)
        self.assertIn("2px min(794px, calc(100% - 48px)) 2px", page)
        self.assertIn(".focus-write-page::before", css)
        self.assertIn(".focus-write-page::after", css)
        rules = css.split(".focus-write-page::before,", 1)[1].split(".focus-write-sheet {", 1)[0]
        self.assertIn("background: #d8b996", rules)
        self.assertIn("position: sticky", rules)
        self.assertIn("height: 100%", rules)
        card = css.split("\n.focus-write-card {", 1)[1].split("}", 1)[0]
        self.assertIn("background: var(--paper, #f7f1e8)", card)
        editor = css.split("\n.focus-write-editor {", 1)[1].split("}", 1)[0]
        self.assertIn("outline: none !important", editor)
        self.assertIn("color: var(--page-ink, var(--ink)) !important", editor)
        self.assertIn("caret-color: var(--page-ink, var(--ink)) !important", editor)
        self.assertNotIn("color: inherit", editor)
        self.assertNotIn("color: #1c1917 !important", editor)
        chalkboard = css.split('body[data-page-theme="chalkboard"] {', 1)[1].split("}", 1)[0]
        self.assertIn("--page-ink: #ffffff", chalkboard)
        self.assertIn("--page-bg: #1f4d2e", chalkboard)
        classroom = css.split("html[data-theme=\"classroom\"],", 1)[1].split("html[data-theme=\"attic\"]", 1)[0]
        self.assertIn("--page-ink: #ffffff", classroom)
        self.assertIn("--page-bg: #1f4d2e", classroom)
        open_fn = self.js.split("function openFocusWrite()", 1)[1].split("function closeFocusWrite()", 1)[0]
        self.assertIn("applyReadablePageInk(pageTheme, pagePref.customColor)", open_fn)
        sheet = css.split("\n.focus-write-sheet {", 1)[1].split("}", 1)[0]
        self.assertIn("grid-column: 3", sheet)
        self.assertNotIn("border-left: 2px solid #d8b996", sheet)
        self.assertNotIn("background: #ffffff", sheet)
        self.assertNotIn("1400px", sheet)
        self.assertIn("794px", css.split(".pdf-sheet {", 1)[1].split("}", 1)[0])
        self.assertIn("1123px", css.split(".pdf-sheet {", 1)[1].split("}", 1)[0])
        self.assertIn(
            ".focus-write-modal.is-fullscreen:has(#focusWriteZoomButton[aria-expanded=\"true\"]) .focus-write-head",
            css,
        )


if __name__ == "__main__":
    unittest.main()
