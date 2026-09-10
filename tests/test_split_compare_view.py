"""동일 문서 비교 보기: 기존 다른-문서 분할을 바꾸지 않고 휘발성 스냅샷 패널을 연다."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SplitCompareViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        cls.ko = json.loads((ROOT / "web" / "locales" / "ko.json").read_text(encoding="utf-8"))
        cls.en = json.loads((ROOT / "web" / "locales" / "en.json").read_text(encoding="utf-8"))
        cls.es = json.loads((ROOT / "web" / "locales" / "es.json").read_text(encoding="utf-8"))

    def test_entry_button_is_outside_existing_split_menu(self) -> None:
        self.assertIn('id="compareSplitButton"', self.html)
        self.assertIn('id="focusWriteCompareButton"', self.html)
        split_menu = self.html.split('id="viewModeDropdown"', 1)[1].split(
            'id="compareSplitButton"', 1
        )[0]
        self.assertNotIn('data-view-mode="compare"', split_menu)
        self.assertIn('data-view-mode="split"', split_menu)
        self.assertIn('data-view-mode="popup"', split_menu)
        self.assertIn('id="formatSplitControl"', self.html)

    def test_compare_chrome_and_refresh_exist(self) -> None:
        self.assertIn('id="splitCompareChrome"', self.html)
        self.assertIn('id="refreshCompareSplitButton"', self.html)
        self.assertIn('data-i18n="app.비교_보기_고정됨"', self.html)
        self.assertIn("#splitViewer.is-compare-view .split-compare-chrome", self.css)
        self.assertIn("#splitViewer.is-compare-view .split-edit-mode-group", self.css)
        self.assertIn("#splitViewer.is-compare-view .split-scene-label", self.css)

    def test_compare_reuses_split_kind_without_persistence(self) -> None:
        self.assertIn('splitKind: "scene"', self.js)
        self.assertIn("function isCompareSplitActive", self.js)
        self.assertIn("function openCompareSplit", self.js)
        self.assertIn("function refreshCompareSplit", self.js)
        self.assertIn("state.splitCompareHtml", self.js)
        self.assertIn('state.splitKind = "compare"', self.js)
        self.assertNotIn("localStorage.setItem", self.js.split("function openCompareSplit", 1)[1].split(
            "function refreshCompareSplit", 1
        )[0])
        open_fn = self.js.split("function openCompareSplit", 1)[1].split(
            "function refreshCompareSplit", 1
        )[0]
        self.assertIn("capturePrimaryManuscriptHtml()", open_fn)
        self.assertNotIn("/api/", open_fn)
        self.assertNotIn("persistSplitScene", open_fn)

    def test_scene_switch_closes_compare_only(self) -> None:
        open_scene = self.js.split("async function openScene(", 1)[1].split(
            "const CHARACTER_ROLE_LABELS", 1
        )[0]
        self.assertIn('state.splitKind === "compare"', open_scene)
        self.assertIn("await closeSplitView()", open_scene)
        swap = 'state.splitEnabled && state.splitSceneId === state.sceneId && previousSceneId'
        self.assertIn(swap, open_scene)

    def test_existing_other_document_split_entry_points_remain(self) -> None:
        self.assertIn("async function openSecondaryView", self.js)
        self.assertIn("async function handleViewModeChoice", self.js)
        self.assertIn("function applySplitDefaultOrOpenMenu", self.js)
        self.assertIn('id="splitViewButton"', self.html)
        self.assertIn('id="splitSceneSelect"', self.html)
        self.assertIn('id="splitReadModeButton"', self.html)
        self.assertIn('id="splitEditModeButton"', self.html)

    def test_locale_keys_exist(self) -> None:
        keys = (
            "app.비교_보기로_열기",
            "app.비교_보기_고정됨",
            "app.비교_보기_새로고침",
            "app.비교_보기를_열었어요",
            "app.비교_보기를_새로고침했어요",
            "app.이미_분할된_상태에서는_비교_보기를_열_수_없어요",
            "index.비교_보기",
        )
        for key in keys:
            for locale in (self.ko, self.en, self.es):
                self.assertIn(key, locale)
                self.assertTrue(str(locale[key]).strip())


if __name__ == "__main__":
    unittest.main()
