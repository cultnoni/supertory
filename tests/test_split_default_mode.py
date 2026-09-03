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


if __name__ == "__main__":
    unittest.main()
