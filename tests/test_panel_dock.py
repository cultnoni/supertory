"""Collapsed side panels become 48px docks that reuse idea float chrome."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class PanelDockContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.locales = {
            lang: json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            for lang in ("ko", "en", "es")
        }

    def test_old_reopen_handles_are_gone(self) -> None:
        self.assertNotIn("binder-panel-reopen", self.html)
        self.assertNotIn("ai-panel-reopen", self.html)
        self.assertNotIn("binder-panel-reopen", self.css)
        self.assertNotIn("ai-panel-reopen", self.css)

    def test_rails_live_inside_side_panels(self) -> None:
        self.assertIn('id="binderDockRail"', self.html)
        self.assertIn('id="aiDockRail"', self.html)
        self.assertIn('id="expandBinderPanelButton"', self.html)
        self.assertIn('id="expandAiPanelButton"', self.html)
        self.assertIn('class="panel-dock-expand"', self.html)
        self.assertIn('data-dock-item="ideas"', self.html)
        self.assertIn('data-dock-item="manuscript"', self.html)
        self.assertNotIn('data-dock-item="priority"', self.html)
        self.assertIn('data-dock-item="toryChat"', self.html)
        self.assertIn('data-dock-item="characterChat"', self.html)
        self.assertIn('data-dock-item="readerChat"', self.html)
        self.assertIn('data-dock-item="aiResult"', self.html)
        self.assertIn('data-dock-item="aiHistory"', self.html)
        self.assertIn('data-dock-item="credits"', self.html)
        self.assertIn('data-dock-item="screenProtect"', self.html)
        self.assertIn('data-dock-item="toryTalk"', self.html)
        self.assertNotIn('data-dock-item="tools"', self.html)
        self.assertNotIn('data-dock-item="notify"', self.html)
        self.assertNotIn('data-dock-item="intro"', self.html)
        self.assertNotIn('data-dock-item="logsyn"', self.html)
        self.assertNotIn('data-dock-item="keywords"', self.html)
        self.assertNotIn('data-dock-item="readingInvite"', self.html)
        self.assertIn('data-settings-section="readingInvite"', self.html)
        self.assertIn('data-settings-main="readingInvite"', self.html)

    def test_dock_rail_toggle_and_active_state(self) -> None:
        timeline = self.html.split('data-dock-item="timeline"', 1)[1].split("</button>", 1)[0]
        self.assertIn('viewBox="0 0 24 24"', timeline)
        self.assertIn('stroke="currentColor"', timeline)
        self.assertIn('<rect x="16" y="16" width="6" height="6" rx="1"/>', timeline)
        self.assertIn('<path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/>', timeline)
        self.assertNotIn("panel-dock-icon-mask", timeline)
        self.assertIn("function toggleDockFloat(", self.js)
        self.assertIn("function syncDockRailButtons(", self.js)
        self.assertIn("function isDockRailItemActive(", self.js)
        self.assertIn("toggleDockFloat(item.dataset.dockItem, item)", self.js)
        self.assertNotIn("openDockFloat(item.dataset.dockItem, item)", self.js.split("function setupPanelDock", 1)[1].split("function refreshAiStatus", 1)[0])
        self.assertIn("expandBinderPanelButton", self.js)
        self.assertIn("setBinderPanelOpen(true)", self.js)
        toggle_fn = self.js.split("function toggleDockFloat(", 1)[1].split("function dockTrackerFallbackPos(", 1)[0]
        self.assertIn("isAiDockPanelItem(itemId)", toggle_fn)
        self.assertIn("toggleAiDockPanelItem(itemId, sourceEl)", toggle_fn)
        self.assertIn("isAiDockFloatItem(itemId)", toggle_fn)
        self.assertIn("raiseIdeaFloat(win)", toggle_fn)
        self.assertIn("closeIdeaFloat(key)", toggle_fn)
        self.assertIn("return openDockFloat(itemId, sourceEl)", toggle_fn)
        self.assertIn("function toggleAiDockPanelItem(", self.js)
        priority_case = self.js.split('case "priority":', 1)[1].split('case "toryTalk":', 1)[0]
        self.assertIn("setToryPriorityOpen(true, sourceEl)", priority_case)
        self.assertNotIn("setAiPanelOpen(true)", priority_case)
        self.assertIn("credits:", self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0])
        self.assertIn("readerFavoritePanel", self.html)
        self.assertIn("function toggleReaderFavorite(", self.js)
        self.assertIn("READER_FAVORITE_MAX = 6", self.js)
        self.assertNotIn("function rememberReaderFavorite(", self.js)
        right_rail = self.html.split('id="aiDockRail"', 1)[1].split("</nav>", 1)[0]
        right_items = [
            item.split('"', 1)[0]
            for item in right_rail.split('data-dock-item="')[1:]
        ]
        self.assertEqual(
            right_items,
            [
                "writingTimer",
                "toryCheck",
                "statsTracker",
                "toryChat",
                "characterChat",
                "readerChat",
                "aiResult",
                "aiHistory",
                "toryTalk",
                "credits",
                "screenProtect",
            ],
        )
        dock_svg_css = self.css.split(".panel-dock-expand svg,\n.panel-dock-item svg {", 1)
        if len(dock_svg_css) < 2:
            dock_svg_css = self.css.split(".panel-dock-item svg {", 1)
        self.assertGreaterEqual(len(dock_svg_css), 2)
        dock_svg_block = dock_svg_css[1].split("}", 1)[0]
        self.assertIn("width: 18px", dock_svg_block)
        self.assertIn("height: 18px", dock_svg_block)
        self.assertIn("stroke-width: 1.7", dock_svg_block)
        self.assertIn("overflow: visible", dock_svg_block)
        self.assertIn('id="toryPriorityBox"', self.html)
        active_fn = self.js.split("function isDockRailItemActive(", 1)[1].split("function syncDockRailButtons(", 1)[0]
        self.assertIn("itemId === \"characters\"", active_fn)
        self.assertIn("DOCK_CHAR_KEY_PREFIX", active_fn)
        self.assertIn("btn.classList.toggle(\"is-open\", on)", self.js.split("function syncDockRailButtons(", 1)[1].split("function syncDockStatsTrackerButton(", 1)[0])
        self.assertIn("if (id === DOCK_STATS_TRACKER_KEY) setDockTrackerOpenPref(false)", self.js)
        self.assertIn(".panel-dock-item.is-open", self.css)
        self.assertIn(".panel-dock-expand svg", self.css)
        self.assertIn(".panel-dock-item svg", self.css)

    def test_ai_rail_items_open_dock_floats(self) -> None:
        panel_items = self.js.split("const AI_DOCK_PANEL_ITEMS = new Set([", 1)[1].split("]);", 1)[0]
        self.assertNotIn("priority", panel_items)
        self.assertIn("toryTalk", panel_items)
        self.assertNotIn("toryChat", panel_items)
        self.assertNotIn("characterChat", panel_items)
        self.assertNotIn("readerChat", panel_items)
        self.assertNotIn("aiResult", panel_items)
        self.assertNotIn("aiHistory", panel_items)
        keys = self.js.split("const DOCK_RAIL_FLOAT_KEYS = {", 1)[1].split("};", 1)[0]
        self.assertIn("toryChat: DOCK_TORY_CHAT_KEY", keys)
        self.assertIn("characterChat: DOCK_CHARACTER_CHAT_KEY", keys)
        self.assertIn("readerChat: DOCK_READER_CHAT_KEY", keys)
        self.assertIn("aiResult: DOCK_AI_RESULT_KEY", keys)
        self.assertIn("aiHistory: DOCK_AI_HISTORY_KEY", keys)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        for name, window_class in (
            ("toryChat", "dock-float-tory-chat"),
            ("characterChat", "dock-float-character-chat"),
            ("readerChat", "dock-float-reader-chat"),
            ("aiResult", "dock-float-ai-result"),
            ("aiHistory", "dock-float-ai-history"),
        ):
            self.assertIn(f"{name}:", spec)
            chunk = spec.split(f"{name}:", 1)[1].split("},", 1)[0]
            self.assertIn(window_class, chunk)
            self.assertIn("resize:", chunk)
        self.assertIn("function adoptDockNode(", self.js)
        self.assertIn("function restoreDockAiHosts(", self.js)
        self.assertIn("function syncAiDockChatHosts(", self.js)
        self.assertIn('id="aiResultHistoryContent"', self.html)
        self.assertIn(".idea-float.dock-float.dock-float-ai-chat", self.css)
        active_fn = self.js.split("function isDockRailItemActive(", 1)[1].split(
            "const AI_DOCK_PANEL_ITEMS", 1
        )[0]
        self.assertNotIn("isAiDockChatHubActive", active_fn)
        self.assertNotIn("isAiDockToolsPaneActive", active_fn)
        ensure_fn = self.js.split("function ensureAiResultVisible(", 1)[1].split(
            "function ensureAiHelperSelectPane(", 1
        )[0]
        self.assertIn("setAiHelperPane", ensure_fn)
        self.assertNotIn('openDockFloat("aiResult")', ensure_fn)
        helper_fn = self.js.split("function setAiHelperPane(", 1)[1].split(
            "function ensureAiResultVisible(", 1
        )[0]
        self.assertIn('pane === "result"', helper_fn)
        self.assertNotIn('openDockFloat("aiResult")', helper_fn)
        history_open = self.js.split("function openAiResultHistoryModal(", 1)[1].split(
            "function closeAiResultHistoryModal(", 1
        )[0]
        self.assertIn("toggleAiPanelHistoryView()", history_open)
        self.assertNotIn('openDockFloat("aiHistory")', history_open)
        self.assertIn("function setAiPanelHistoryOpen(", self.js)
        self.assertIn('id="aiResultHistoryPane"', self.html)
        self.assertIn('id="aiPanelHistoryList"', self.html)
        self.assertNotIn('aria-haspopup="dialog"', self.html.split('id="aiResultHistoryButton"', 1)[1].split("</button>", 1)[0])
        toggle_ai = self.js.split("function toggleAiDockPanelItem(", 1)[1].split(
            "function toggleDockFloat(", 1
        )[0]
        self.assertNotIn('case "toryChat":', toggle_ai)
        self.assertNotIn('case "aiResult":', toggle_ai)

    def test_tory_check_dock_widget(self) -> None:
        self.assertRegex(
            self.html,
            r'class="panel-dock-item is-ready"[^>]*data-dock-item="toryCheck"',
        )
        tory_check = self.html.split('data-dock-item="toryCheck"', 1)[1].split("</button>", 1)[0]
        self.assertIn('viewBox="0 0 24 24"', tory_check)
        self.assertIn('stroke="currentColor"', tory_check)
        self.assertIn("M3.85 8.62a4 4 0 0 1 4.78-4.77", tory_check)
        self.assertIn("m16 9-5.5 5.5L8 12", tory_check)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        self.assertIn("toryCheck:", spec)
        check_spec = spec.split("toryCheck:", 1)[1].split("};", 1)[0]
        self.assertIn('windowClass: "dock-float-tory-check"', check_spec)
        self.assertIn(
            "resize: { minWidth: DOCK_TORY_CHECK_MIN_W, minHeight: DOCK_TORY_CHECK_MIN_H }",
            check_spec,
        )
        self.assertIn("toryCheck: DOCK_TORY_CHECK_KEY", self.js)
        self.assertIn("function scheduleToryCheckRefresh(", self.js)
        self.assertIn("function runToryCheckActiveTab(", self.js)
        run_fn = self.js.split("function runToryCheckActiveTab(", 1)[1].split(
            "async function loadToryCheckSettings(", 1
        )[0]
        self.assertIn("engine.analyze(tab, getEditorPlainText()", run_fn)
        self.assertNotIn("TABS.forEach", run_fn)
        self.assertNotIn("for (const tab of", run_fn)
        schedule_fn = self.js.split("function scheduleToryCheckRefresh(", 1)[1].split(
            "function toryCheckTabIcon(", 1
        )[0]
        self.assertIn("engine?.DEBOUNCE_MS || 400", schedule_fn)
        self.assertIn("scheduleToryCheckRefresh();", self.js.split("function updateSceneStats()", 1)[1].split("/* —— Goal gauge colors", 1)[0])
        icon_fn = self.js.split("function toryCheckTabIcon(", 1)[1].split(
            "function toryCheckFloatBody(", 1
        )[0]
        self.assertIn('m17 2 4 4-4 4', icon_fn)
        self.assertIn('rect x="3" y="14" width="7" height="7" rx="1"', icon_fn)
        self.assertIn('m12 16 4-4-4-4', icon_fn)
        self.assertIn("M16 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1", icon_fn)
        self.assertIn("M14 2v5a1 1 0 0 0 1 1h5", icon_fn)
        self.assertIn("M10 12a1 1 0 0 0-1 1v1a1 1 0 0 1-1 1", icon_fn)
        self.assertNotIn("M12 17h.01", icon_fn)
        self.assertNotIn("M8 6h13", icon_fn)
        self.assertIn(".idea-float.dock-float.dock-float-tory-check", self.css)
        self.assertIn('id="toryCheckViewpointModal"', self.html)
        self.assertIn("/api/projects/${pid}/tory-check", self.js)
        self.assertNotIn("toryCheck", self.js.split("const AI_DOCK_PANEL_ITEMS = new Set([", 1)[1].split("]);", 1)[0])
        for locale in self.locales.values():
            for key in (
                "index.실시간_토리_체크",
                "index.반복_단어",
                "index.같은_표현",
                "index.문장_시작",
                "index.연속_대사",
                "index.수식어",
                "index.감탄사",
                "index.시점",
                "index.금칙어",
                "index.엄격",
                "index.보통",
                "index.느슨",
                "index.먼저_시점을_설정해주세요",
                "index.먼저_금칙어를_추가해주세요",
            ):
                self.assertIn(key, locale)

    def test_appearances_dock_widget(self) -> None:
        left_rail = self.html.split('id="binderDockRail"', 1)[1].split("</nav>", 1)[0]
        left_items = [
            item.split('"', 1)[0]
            for item in left_rail.split('data-dock-item="')[1:]
        ]
        self.assertEqual(left_items.index("appearances"), left_items.index("items") + 1)
        self.assertEqual(left_items.index("dictionary"), left_items.index("appearances") + 1)
        self.assertEqual(left_items.index("baits"), left_items.index("dictionary") + 1)
        self.assertEqual(left_items[6], "appearances")
        self.assertNotIn("writingTimer", left_items)
        self.assertNotIn("statsTracker", left_items)
        self.assertNotIn("toryCheck", left_items)
        appearances = left_rail.split('data-dock-item="appearances"', 1)[1].split("</button>", 1)[0]
        self.assertIn('viewBox="0 0 24 24"', appearances)
        self.assertIn('stroke="currentColor"', appearances)
        self.assertIn("M18 8c0 3.613-3.869 7.429-5.393 8.795a1 1 0 0 1-1.214 0C9.87 15.429 6 11.613 6 8a6 6 0 0 1 12 0", appearances)
        self.assertIn("M8.714 14h-3.71a1 1 0 0 0-.948.683l-2.004 6A1 1 0 0 0 3 22h18a1 1 0 0 0 .948-1.316l-2-6a1 1 0 0 0-.949-.684h-3.712", appearances)
        self.assertIn('width="18"', appearances)
        self.assertIn('stroke-width="1.7"', appearances)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        self.assertIn("appearances:", spec)
        appearance_spec = spec.split("appearances:", 1)[1].split("};", 1)[0]
        self.assertIn('windowClass: "dock-float-appearances"', appearance_spec)
        self.assertIn("appearances: DOCK_APPEARANCES_KEY", self.js)
        self.assertIn("function openDockAppearancesFloat(", self.js)
        self.assertIn("function paintDockAppearancesList(", self.js)
        self.assertIn("openChronicleScene(button.getAttribute(\"data-dock-appearance-scene\")", self.js)
        self.assertIn("/api/projects/${pid}/character-appearances?character_id=${characterId}", self.js)
        self.assertIn("openDockAppearancesFloat(data.id, event.currentTarget)", self.js)
        self.assertIn('data-role="dock-char-appearances"', self.js)
        self.assertIn(".idea-float.dock-float.dock-float-appearances", self.css)
        self.assertIn("dock-appearance-snippet", self.js)
        self.assertIn("dock-appearance-line", self.js)
        self.assertIn(".dock-appearance-item.is-latest", self.css)
        self.assertIn('dockGuideTipHtml("dockAppearances"', self.js)
        self.assertIn('id: "dockAppearances"', self.js)
        for locale in self.locales.values():
            for key in (
                "index.등장_이력",
                "index.등장_이력_보기",
                "index.등장_이력_인물_필터",
                "index.등장_이력_안내",
                "index.등장_이력_없음",
                "index.최근_등장",
                "index.마지막_대사",
            ):
                self.assertIn(key, locale)

    def test_dictionary_dock_widget(self) -> None:
        left_rail = self.html.split('id="binderDockRail"', 1)[1].split("</nav>", 1)[0]
        left_items = [
            item.split('"', 1)[0]
            for item in left_rail.split('data-dock-item="')[1:]
        ]
        self.assertEqual(left_items.index("dictionary"), left_items.index("appearances") + 1)
        self.assertEqual(left_items.index("baits"), left_items.index("dictionary") + 1)
        manuscript = left_rail.split('data-dock-item="manuscript"', 1)[1].split("</button>", 1)[0]
        dictionary = left_rail.split('data-dock-item="dictionary"', 1)[1].split("</button>", 1)[0]
        vault = left_rail.split('data-dock-item="toryVault"', 1)[1].split("</button>", 1)[0]
        self.assertIn('m6 14 1.45-2.9A2 2 0 0 1 9.24 10H20', manuscript)
        self.assertIn('circle cx="14" cy="15" r="1"', manuscript)
        self.assertNotIn("M12 5v16", manuscript)
        self.assertIn("M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19", dictionary)
        self.assertIn("m8 13 4-7 4 7", dictionary)
        self.assertIn("M9.1 11h5.7", dictionary)
        self.assertNotIn("M12 5v16", dictionary)
        self.assertNotIn("m16 12 2 2 4-4", dictionary)
        self.assertIn('title="토리 사전"', dictionary)
        settings_dictionary = self.html.split('data-settings-section="dictionary"', 1)[1].split("</section>", 1)[0]
        self.assertIn("m8 13 4-7 4 7", settings_dictionary)
        self.assertIn("M9.1 11h5.7", settings_dictionary)
        self.assertIn('rect width="20" height="5" x="2" y="3" rx="1"', vault)
        self.assertIn('path d="M10 12h4"', vault)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        self.assertIn("dictionary:", spec)
        dictionary_spec = spec.split("dictionary:", 1)[1].split("baits:", 1)[0]
        self.assertIn('windowClass: "dock-float-dictionary"', dictionary_spec)
        self.assertIn("resize: { minWidth: DOCK_DICTIONARY_MIN_W, minHeight: DOCK_DICTIONARY_MIN_H }", dictionary_spec)
        self.assertIn("dictionary: DOCK_DICTIONARY_KEY", self.js)
        self.assertIn("function renderDockDictionaryBody(", self.js)
        self.assertIn("data-role=\"dock-dictionary-add\"", self.js)
        self.assertIn("data-dock-dictionary-edit", self.js)
        self.assertIn("function addToryDictionaryFromSelection(", self.js)
        self.assertIn('data-context-action="add-tory-dict"', self.html)
        self.assertIn(".idea-float.dock-float.dock-float-dictionary", self.css)
        lookup = self.html.find('data-context-action="lookup-dict"')
        similar = self.html.find('data-context-action="similar-words"')
        add_dict = self.html.find('data-context-action="add-tory-dict"')
        cross = self.html.find('data-context-action="cross-ref-search"')
        self.assertLess(lookup, similar)
        self.assertLess(similar, add_dict)
        self.assertLess(add_dict, cross)
        self.assertIn('data-context-action="toggle-dict-highlight"', self.html)
        self.assertIn("function toggleDictHighlight(", self.js)
        self.assertIn("supertory.dictHighlight.", self.js)
        for locale in self.locales.values():
            self.assertIn("app.토리_사전", locale)
            self.assertIn("index.토리_사전에_추가", locale)
            self.assertIn("index.이미_kinds_에_같은_이름이_있어요", locale)
        self.assertEqual(self.locales["ko"]["app.토리_사전"], "토리 사전")
        self.assertEqual(self.locales["ko"]["index.열린_떡밥"], "떡밥 모음")
        self.assertIn('dockGuideTipHtml("dockDictionary"', self.js)
        self.assertIn("function dockGuideTipHtml(", self.js)
        self.assertIn('id: "dockDictionary"', self.js)

    def test_stats_tracker_is_pinned_dock_widget(self) -> None:
        self.assertIn('data-dock-item="statsTracker"', self.html)
        stats_tracker = self.html.split('data-dock-item="statsTracker"', 1)[1].split("</button>", 1)[0]
        self.assertIn('path d="M4 12V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.706.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2"', stats_tracker)
        self.assertIn('rect x="2" y="16" width="4" height="6" rx="2"', stats_tracker)
        self.assertNotIn("is-pinned-dock", self.html)
        self.assertNotIn("panel-dock-pin", self.html)
        self.assertNotIn(".panel-dock-item.is-pinned-dock", self.css)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        self.assertIn("statsTracker:", spec)
        self.assertIn("function isDockTrackerOpenPref()", self.js)
        self.assertIn('supertory.dock.statsTracker.open', self.js)
        self.assertIn("supertory.dock.statsTracker.layout", self.js)
        self.assertIn("if (isDockTrackerOpenPref()) openDockFloat(\"statsTracker\")", self.js)
        self.assertIn("if (id === DOCK_STATS_TRACKER_KEY) continue;", self.js)
        stats_fn = self.js.split("function updateSceneStats()", 1)[1].split("/* —— Goal gauge colors", 1)[0]
        self.assertIn("syncDockStatsTracker();", stats_fn)
        self.assertIn("lastStatusBarCountSnapshot = {", stats_fn)
        self.assertIn("scope: isProject ? \"project\" : \"current\"", stats_fn)
        self.assertIn("space: spaceModeFromMetric(metric)", stats_fn)
        self.assertIn("updateGoalProgressUi(lastStatusBarCountSnapshot);", stats_fn)
        tracker_fn = self.js.split("function dockStatsFlagLabels", 1)[1].split("function syncDockStatsTracker", 1)[0]
        self.assertIn("function renderDockStatsTracker", tracker_fn)
        self.assertIn("dockStatsFlagLabels(snapshot)", tracker_fn)
        self.assertIn("dock-stats-flags", tracker_fn)
        self.assertIn("app.글자_공포", tracker_fn)
        self.assertIn("app.글자_공제", tracker_fn)
        self.assertIn("app.현재", tracker_fn)
        self.assertIn("app.전체", tracker_fn)
        self.assertNotIn("sceneStats[metric]", stats_fn)
        self.assertNotIn("lastSceneCountSnapshot", self.js)
        self.assertNotIn("computeTextStats(", self.js.split("function renderDockStatsTracker", 1)[1].split("function syncDockStatsTracker", 1)[0])
        self.assertIn(".idea-float.dock-float.dock-float-mini", self.css)
        for locale in self.locales.values():
            self.assertIn("app.글자수_트래커", locale)
            self.assertIn("app.글자_공포", locale)
            self.assertIn("app.글자_공제", locale)
            self.assertIn("index.남은_분량", locale)
        self.assertEqual(self.locales["ko"]["app.글자_공포"], "공백포함")
        self.assertEqual(self.locales["ko"]["app.글자_공제"], "공백제외")
        self.assertIn(".dock-stats-flags", self.css)

    def test_writing_timer_dock_widget(self) -> None:
        self.assertIn('data-dock-item="writingTimer"', self.html)
        self.assertIn('data-i18n-title="app.기록_타이머"', self.html.split('data-dock-item="writingTimer"', 1)[1].split("</button>", 1)[0])
        writing_timer = self.html.split('data-dock-item="writingTimer"', 1)[1].split("</button>", 1)[0]
        self.assertIn("M5 22h14", writing_timer)
        self.assertIn("M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22", writing_timer)
        self.assertIn('stroke="currentColor"', writing_timer)
        tool_timer = self.html.split('id="writingLogButton"', 1)[1].split("</button>", 1)[0]
        self.assertIn('<circle cx="12" cy="12" r="8.25"/>', tool_timer)
        self.assertIn("M12 7.5V12l3 2", tool_timer)
        self.assertIn('data-i18n="app.기록"', tool_timer)
        self.assertIn('name="writingTimerStylePref"', self.html)
        self.assertIn('value="hourglass"', self.html)
        self.assertIn('value="alarm"', self.html)
        self.assertNotIn('value="stopwatch"', self.html)
        self.assertIn('value="digits"', self.html)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        self.assertIn("writingTimer:", spec)
        writing_spec = spec.split("writingTimer:", 1)[1].split("characters:", 1)[0]
        self.assertIn('windowClass: "dock-float-writing-timer"', writing_spec)
        self.assertIn('titleKey: "app.기록_타이머"', writing_spec)
        self.assertIn("compact: true", writing_spec)
        self.assertIn("function renderDockWritingTimer(", self.js)
        self.assertIn("function syncDockWritingTimer(", self.js)
        self.assertIn("function togglePomodoro(", self.js)
        self.assertIn("function dockCountdownHtml(", self.js)
        self.assertIn("function finishPomodoroPhase(", self.js)
        self.assertIn("function completePomodoro(", self.js)
        self.assertIn("function playPomodoroChime(", self.js)
        self.assertIn("function popupWritingTimerOnAlarm(", self.js)
        popup_fn = self.js.split("function popupWritingTimerOnAlarm(", 1)[1].split("function completePomodoro(", 1)[0]
        self.assertIn('openDockFloat("writingTimer"', popup_fn)
        complete_fn = self.js.split("function completePomodoro(", 1)[1].split("function finishPomodoroPhase(", 1)[0]
        self.assertIn("popupWritingTimerOnAlarm()", complete_fn)
        finish_fn = self.js.split("function finishPomodoroPhase(", 1)[1].split("function selectPomodoroPreset(", 1)[0]
        self.assertIn("popupWritingTimerOnAlarm()", finish_fn)
        self.assertGreaterEqual(finish_fn.count("popupWritingTimerOnAlarm()"), 3)
        self.assertIn("supertory.dock.writingTimer.open", self.js)
        self.assertIn("supertory.writingTimerStyle", self.js)
        self.assertIn("supertory.pomodoro.presets", self.js)
        self.assertIn("supertory.pomodoro.activeId", self.js)
        self.assertIn("POMODORO_MAX_PRESETS = 8", self.js)
        self.assertIn("function defaultTimerPresets(", self.js)
        self.assertIn('writeMin: 25', self.js)
        self.assertIn("breakMin: 5", self.js)
        self.assertIn("sets: 4", self.js)
        self.assertIn("writeMin: 50", self.js)
        self.assertIn("breakMin: 10", self.js)
        self.assertIn("writeMin: 52", self.js)
        self.assertIn("breakMin: 17", self.js)
        self.assertIn("writeMin: 90", self.js)
        self.assertIn("breakMin: 20", self.js)
        self.assertIn("function formatTimerDuration(", self.js)
        self.assertIn('if (h > 0) return i18n.t("app.타이머_시분"', self.js)
        self.assertIn('if (isDockWritingTimerOpenPref()) openDockFloat("writingTimer")', self.js)
        self.assertIn("if (id === DOCK_WRITING_TIMER_KEY) continue;", self.js)
        render_fn = self.js.split("function renderDockWritingTimer(", 1)[1].split("function syncDockWritingTimer(", 1)[0]
        countdown_fn = self.js.split("function dockCountdownHtml(", 1)[1].split("function bindDockWritingTimerBody(", 1)[0]
        self.assertIn("dock-timer-record", render_fn)
        self.assertIn("data-role=\"dock-timer-readout\"", render_fn)
        self.assertIn("dockCountdownHtml()", render_fn)
        self.assertLess(
            render_fn.find("dock-timer-record"),
            render_fn.find("dockCountdownHtml()"),
        )
        self.assertNotIn("dock-timer-style-picks", render_fn)
        self.assertIn('dockTimerCycleSegHtml("style"', countdown_fn)
        self.assertIn('dockTimerCycleSegHtml("display"', countdown_fn)
        self.assertIn('dockTimerCycleSegHtml("sound"', countdown_fn)
        self.assertIn("dock-timer-face", countdown_fn)
        self.assertLess(
            countdown_fn.find("app.포모도로_타이머"),
            countdown_fn.find("dock-timer-quick"),
        )
        self.assertLess(
            countdown_fn.find("dock-timer-quick"),
            countdown_fn.find("dock-timer-face"),
        )
        self.assertLess(
            countdown_fn.find("dock-countdown-readout"),
            countdown_fn.find("dock-timer-toggles"),
        )
        self.assertLess(
            countdown_fn.find("dock-timer-toggles"),
            countdown_fn.find("dock-countdown-presets"),
        )
        self.assertLess(
            countdown_fn.find("dock-timer-face"),
            countdown_fn.find("dock-timer-toggles"),
        )
        self.assertLess(
            countdown_fn.find("dock-timer-face"),
            countdown_fn.find('dockTimerCycleSegHtml("style"'),
        )
        self.assertLess(
            countdown_fn.find('dockTimerCycleSegHtml("style"'),
            countdown_fn.find('dockTimerCycleSegHtml("display"'),
        )
        self.assertLess(
            countdown_fn.find('dockTimerCycleSegHtml("display"'),
            countdown_fn.find('dockTimerCycleSegHtml("sound"'),
        )
        self.assertIn("dock-countdown.is-session .dock-timer-display-picks", self.css)
        self.assertIn(".dock-countdown.is-session .dock-timer-quick {", self.css)
        self.assertNotIn(
            "dock-countdown.is-session .dock-pomodoro-phase",
            self.css.split(".dock-countdown.is-session .dock-countdown-title", 1)[1].split(".dock-countdown.is-session {", 1)[0],
        )
        self.assertIn(".dock-countdown-session-actions", self.css)
        self.assertIn(".dock-countdown.is-session .dock-countdown-session-actions", self.css)
        self.assertIn(".dock-countdown-text-btn", self.css)
        self.assertIn("dock-countdown-session-actions", countdown_fn)
        self.assertIn("dockTimerQuickStartHtml(running)", countdown_fn)
        self.assertIn("dock-pomodoro-face", countdown_fn)
        self.assertIn("is-session", countdown_fn)
        self.assertIn("is-timer-session", self.js)
        self.assertNotIn("dock-countdown-toggle", countdown_fn)
        self.assertNotIn("dock-countdown-clear", countdown_fn)
        self.assertIn("dock-countdown-reset", countdown_fn)
        self.assertIn("dock-countdown-back", countdown_fn)
        self.assertIn("app.타이머_초기화", countdown_fn)
        self.assertIn("app.타이머_이전화면", countdown_fn)
        self.assertNotIn("function revealWritingTimerSetup(", self.js)
        self.assertNotIn("function pomodoroCompactWidget(", self.js)
        self.assertIn("function startQuickPomodoro(", self.js)
        self.assertIn("function pomodoroClockSeconds(", self.js)
        self.assertIn("function fitDockWritingTimerShell(", self.js)
        self.assertIn("data-role=\"pomodoro-quick-min\"", countdown_fn)
        self.assertIn("dockTimerQuickStartHtml(running)", countdown_fn)
        quick_start_fn = self.js.split("function dockTimerQuickStartHtml(", 1)[1].split("function writingTimerStyleButtonsHtml(", 1)[0]
        self.assertIn("data-role=\"pomodoro-quick-start\"", quick_start_fn)
        self.assertIn("dock-timer-quick-start", quick_start_fn)
        self.assertIn("M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z", self.js)
        self.assertIn('rect x="14" y="3" width="5" height="18" rx="1"', self.js)
        self.assertNotIn("M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", self.js)
        self.assertIn("M9 14 4 9l5-5", self.js)
        self.assertIn("m12 19-7-7 7-7", self.js)
        self.assertIn("dock-timer-${cycle}-picks", self.js)
        self.assertIn("data-role=\"dock-timer-cycle\"", self.js)
        self.assertIn("pomodoroDisplayButtonsHtml(display)", countdown_fn)
        self.assertIn("pomodoroSoundButtonsHtml(sound)", countdown_fn)
        self.assertIn('"pomodoro-display"', self.js)
        self.assertIn('"pomodoro-sound"', self.js)
        self.assertIn("app.타이머_바로_시작", self.js)
        self.assertIn("app.타이머_일시정지", self.js)
        self.assertIn("app.타이머_남은_시간_표시", self.js)
        self.assertIn("DOCK_WRITING_TIMER_MIN_W = 148", self.js)
        self.assertIn("DOCK_WRITING_TIMER_MIN_H = 120", self.js)
        self.assertIn("function syncDockWritingTimerRail(", self.js)
        self.assertIn("function applyDockWritingTimerScale(", self.js)
        self.assertIn("function ensureWritingRecordingOn(", self.js)
        self.assertIn("ensureWritingRecordingOn({ quiet: true })", self.js.split("function startPomodoro(", 1)[1].split("function stopPomodoroSession(", 1)[0])
        self.assertIn("DOCK_WRITING_TIMER_MIN_W", writing_spec)
        self.assertIn("DOCK_WRITING_TIMER_MIN_H", writing_spec)
        self.assertIn("onResize(win)", writing_spec)
        self.assertNotIn("resize: false", writing_spec)
        bind_fn = self.js.split("function bindDockWritingTimerBody(", 1)[1].split(
            "function syncDockCountdownUi(", 1
        )[0]
        self.assertIn("togglePomodoro()", bind_fn)
        self.assertIn("pausePomodoro()", bind_fn)
        self.assertIn("resetPomodoro()", bind_fn)
        self.assertIn("restartPomodoro()", bind_fn)
        self.assertNotIn("revealWritingTimerSetup()", bind_fn)
        self.assertNotIn("dock-countdown-toggle", bind_fn)
        self.assertIn("handleDockTimerCycleClick", bind_fn)
        self.assertIn('addEventListener("contextmenu"', bind_fn)
        self.assertIn("function cycleDockTimerChoice(", self.js)
        self.assertIn("function playPomodoroSoundPatch(", self.js)
        self.assertIn('POMODORO_SOUNDS = ["chime", "ding", "bell"]', self.js)
        self.assertNotIn('"wood"', self.js.split("const POMODORO_SOUNDS", 1)[1].split(";", 1)[0])
        self.assertIn("supertory.pomodoro.sound", self.js)
        self.assertIn('playPomodoroChime("preview")', self.js)
        self.assertNotIn('addEventListener("dblclick"', bind_fn)
        self.assertNotIn("openPomodoroLargeView", self.js)
        self.assertNotIn("schedulePomodoroFaceToggle", self.js)
        self.assertNotIn("syncPomodoroLargeView", self.js)
        self.assertNotIn('id="pomodoroLargeView"', self.html)
        self.assertNotIn("data-close-pomodoro-large", self.html)
        self.assertNotIn(".pomodoro-large-view", self.css)
        self.assertIn('data-role="dock-timer-countdown"', self.html)
        self.assertIn(".panel-dock-timer-countdown", self.css)
        self.assertIn(".dock-timer-quick", self.css)
        self.assertIn(".dock-timer-display-picks", self.css)
        self.assertIn(".dock-timer-sound-picks", self.css)
        self.assertIn("min-width: 148px", self.css.split(".idea-float.dock-float.dock-float-writing-timer {", 1)[1].split("}", 1)[0])
        self.assertIn(".idea-float.dock-float.dock-float-writing-timer.dock-float-mini .idea-float-resize", self.css)
        self.assertIn("return false; // default: 「기록중」", self.js)
        self.assertNotIn("checked", self.html.split('id="writingShowTimer"', 1)[1].split(">", 1)[0])
        self.assertIn("dock-widget-btn", countdown_fn)
        self.assertIn("app.포모도로_타이머", countdown_fn)
        self.assertIn("dock-countdown-title", countdown_fn)
        self.assertIn("pomodoroPresetCardHtml", countdown_fn)
        self.assertIn("data-role=\"dock-pomodoro-save-new\"", countdown_fn)
        self.assertIn("data-role=\"dock-pomodoro-preset\"", self.js)
        self.assertIn("dock-timer-preset-name", self.js)
        self.assertIn("function pomodoroPresetCardHtml(", self.js)
        self.assertIn(".dock-timer-hourglass", self.css)
        self.assertIn(".dock-timer-alarm", self.css)
        self.assertNotIn(".dock-timer-stopwatch", self.css)
        self.assertIn(".dock-timer-digits", self.css)
        self.assertIn("repeat(3, minmax(0, 1fr))", self.css.split(".dock-timer-toggles {", 1)[1].split("}", 1)[0])
        self.assertIn(".dock-timer-seg:not(.is-open) .dock-timer-style-btn:not(.is-active)", self.css)
        self.assertIn(".dock-timer-seg", self.css)
        self.assertIn(".dock-timer-toggles", self.css)
        self.assertIn("role=\"radiogroup\"", self.js)
        self.assertIn("dock-timer-toggles", self.js)
        self.assertIn(".dock-countdown", self.css)
        self.assertIn(".dock-countdown-title", self.css)
        self.assertIn(".is-timer-session", self.css)
        self.assertIn(".dock-timer-preset-name", self.css)
        self.assertIn(".dock-timer-record", self.css)
        self.assertIn(".dock-widget-btn", self.css)
        self.assertIn(".dock-pomodoro-form", self.css)
        self.assertIn("dock-hg-sand", self.css)
        for locale in self.locales.values():
            self.assertIn("app.기록", locale)
            self.assertIn("app.기록_타이머", locale)
            self.assertIn("app.모래시계", locale)
            self.assertIn("app.알람_시계", locale)
            self.assertIn("app.숫자", locale)
            self.assertIn("app.기록_위젯_디자인", locale)
            self.assertIn("app.포모도로_타이머", locale)
            self.assertIn("app.타이머_세트_표준", locale)
            self.assertIn("app.타이머_세트_스프린트", locale)
            self.assertIn("app.타이머_세트_오십이", locale)
            self.assertIn("app.타이머_세트_울트라디안", locale)
            self.assertIn("app.글쓰기_시간_끝_쉬세요", locale)
            self.assertIn("app.쉬는_시간_끝_글쓰기", locale)
            self.assertIn("app.포모도로_세트를_마쳤어요", locale)
            self.assertIn("app.프리셋은_5개까지예요", locale)
            self.assertIn("app.새_프리셋_저장", locale)
            self.assertIn("app.타이머_초기화", locale)
            self.assertIn("app.타이머_이전화면", locale)
            self.assertIn("app.타이머_정지", locale)
            self.assertIn("app.타이머_위젯_진행_안내", locale)
            self.assertIn("app.타이머_남은_시간", locale)
            self.assertIn("app.타이머_지난_시간", locale)
            self.assertIn("app.타이머_바로_시작", locale)
            self.assertIn("app.타이머_일시정지", locale)
            self.assertIn("app.타이머_글쓰기_시간", locale)
            self.assertIn("app.타이머_휴식_시간", locale)
            self.assertIn("app.타이머_남은_시간_표시", locale)
            self.assertIn("app.타이머_지난_시간_표시", locale)
            self.assertIn("app.타이머_알람_소리", locale)
            self.assertIn("app.타이머_알람_종", locale)
            self.assertIn("app.타이머_알람_딩동", locale)
            self.assertIn("app.타이머_알람_벨", locale)
            self.assertNotIn("app.타이머_알람_북", locale)
            self.assertIn("app.타이머_옵션_순환_안내", locale)
        self.assertEqual(self.locales["ko"]["app.타이머_이전화면"], "이전화면")
        self.assertEqual(self.locales["ko"]["app.타이머_남은_시간_표시"], "남은 시간")
        self.assertEqual(self.locales["ko"]["app.타이머_지난_시간_표시"], "지난 시간")
        self.assertEqual(self.locales["ko"]["app.타이머_알람_종"], "종소리")
        self.assertEqual(self.locales["ko"]["app.타이머_알람_딩동"], "딩동")
        self.assertEqual(self.locales["ko"]["app.타이머_알람_벨"], "벨소리")
        self.assertEqual(self.locales["ko"]["app.타이머_일시정지"], "일시정지")
        self.assertEqual(self.locales["ko"]["app.쉬는_시간"], "휴식")
        self.assertEqual(self.locales["ko"]["app.타이머_글쓰기_시간"], "글쓰기 시간")
        self.assertEqual(self.locales["ko"]["app.타이머_휴식_시간"], "휴식 시간")
        self.assertEqual(self.locales["ko"]["app.기록"], "기록")
        self.assertEqual(self.locales["ko"]["app.기록_타이머"], "기록·타이머")
        self.assertEqual(self.locales["ko"]["app.포모도로_타이머"], "타이머")
        self.assertEqual(self.locales["ko"]["app.타이머_세트_표준"], "표준 포모도로 사이클 (추천)")
        self.assertEqual(self.locales["ko"]["app.타이머_세트_스프린트"], "스프린트 세트 (일반 집중)")
        self.assertEqual(self.locales["ko"]["app.타이머_세트_오십이"], "52/17 세트 (지속 집중)")
        self.assertEqual(self.locales["ko"]["app.타이머_세트_울트라디안"], "울트라디안 리듬 (고도 집중)")
        self.assertIn("글쓰기 시간 끝", self.locales["ko"]["app.글쓰기_시간_끝_쉬세요"])

    def test_collapsed_grid_keeps_48px_rails(self) -> None:
        self.assertIn("--panel-dock-rail-w: 48px;", self.css)
        self.assertIn(
            "grid-template-columns: var(--outline-width) minmax(0, 1fr) var(--panel-dock-rail-w, 48px);",
            self.css,
        )
        self.assertIn(
            "grid-template-columns: var(--panel-dock-rail-w, 48px) minmax(0, 1fr) var(--ai-panel-width);",
            self.css,
        )
        self.assertIn(
            "grid-template-columns: var(--panel-dock-rail-w, 48px) minmax(0, 1fr) var(--panel-dock-rail-w, 48px);",
            self.css,
        )
        self.assertNotIn("body.binder-panel-collapsed .outline-panel {\n  display: none;", self.css)
        self.assertNotIn("body.ai-panel-collapsed .ai-panel {\n  display: none;", self.css)
        self.assertNotIn("margin-left: 0;\n  margin-right: auto;", self.css)

    def test_expanded_center_overhangs_one_mm_without_changing_panel_tracks(self) -> None:
        expanded = self.css.split(
            "body:not(.binder-panel-collapsed):not(.ai-panel-collapsed) .work-area {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("width: calc(100% + 2mm)", expanded)
        self.assertIn("margin-left: -1mm", expanded)
        self.assertIn("margin-right: -1mm", expanded)
        self.assertNotIn("--outline-width", expanded)
        self.assertNotIn("--ai-panel-width", expanded)

    def test_split_icon_spacing_tracks_available_width(self) -> None:
        split_icons = self.css.split(
            ".scene-workspace.split-active .format-toolbar-row-icons "
            ".format-toolbar-row-body.format-toolbar-row-body-split {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("justify-content: space-between", split_icons)
        self.assertIn("width: 100%", split_icons)
        self.assertIn("gap: 0", split_icons)
        self.assertNotIn("gap: 8px", split_icons)

    def test_left_panel_keeps_round_edge_when_ai_collapsed(self) -> None:
        collapsed = self.css.split(
            "body.ai-panel-collapsed .outline-panel-inner {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("border-top-right-radius: 14px", collapsed)
        self.assertIn("border-bottom-right-radius: 14px", collapsed)

    def test_collapsed_rails_keep_panel_bottom_inset(self) -> None:
        left = self.css.split(
            "body.binder-panel-collapsed .outline-panel {",
            1,
        )[1].split("}", 1)[0]
        right = self.css.split(
            "body.ai-panel-collapsed .ai-panel {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("padding: 0 0 10px", left)
        self.assertIn("padding: 0 0 10px", right)

    def test_episode_tab_row_starts_level_with_side_panel_cards(self) -> None:
        """Tab row carries the panels' own top inset; the chrome above it is flat.

        Panels use `max(0px, calc(10px - 2mm))` above their card, so the tab bar
        must use the same value and `.episode-chrome` must add nothing on top.
        """
        panel_inset = "max(0px, calc(10px - 2mm))"
        chrome = self.css.split(".episode-chrome {", 1)[1].split("}", 1)[0]
        tab_bar = self.css.split(".episode-tab-bar {", 1)[1].split("}", 1)[0]
        outline = self.css.split("\n.outline-panel {", 1)[1].split("}", 1)[0]

        self.assertIn(f"padding-top: {panel_inset}", outline)
        self.assertIn("padding: 0 var(--ms-h-gutter", chrome)
        self.assertIn(f"padding: {panel_inset} 2px 0", tab_bar)
        # The old 6px lip is what pushed the centre column below the panels.
        self.assertNotIn("padding: 6px 2px 0", tab_bar)
        # Tab size itself must stay untouched.
        tab = self.css.split(".episode-tab {", 1)[1].split("}", 1)[0]
        self.assertIn("min-height: 28px", tab)

    def test_pinned_ideas_stay_on_outline_not_widget_rail(self) -> None:
        left = self.html.split('id="binderDockRail"', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("binderDockRailFooter", left)
        self.assertNotIn("panel-dock-rail-footer", left)
        self.assertNotIn("data-dock-pinned-idea", self.html)
        self.assertNotIn("function renderDockPinnedIdeas(", self.js)
        self.assertIn('id="headerIdeaBar"', self.html)
        self.assertIn('id="headerIdeaNotice"', self.html)
        header_fn = self.js.split("function renderHeaderIdeaBar(", 1)[1].split(
            "function setupHeaderNotices(", 1
        )[0]
        self.assertIn("headerIdeaBar", header_fn)
        self.assertIn("data-header-idea", header_fn)
        self.assertIn("openIdeaFloat", header_fn)
        self.assertNotIn("renderDockPinnedIdeas", header_fn)
        persist_fn = self.js.split("function persistDockRailOrder(", 1)[1].split(
            "function restoreDockRailOrder(", 1
        )[0]
        self.assertNotIn("data-dock-pinned-idea", persist_fn)
        sort_fn = self.js.split("function setupDockRailSorting(", 1)[1].split(
            "function setupPanelDock()", 1
        )[0]
        self.assertNotIn("data-dock-pinned-idea", sort_fn)
        self.assertNotIn(".panel-dock-rail-footer", sort_fn)
        self.assertNotIn(".panel-dock-pinned-idea", self.css)
        self.assertNotIn(".panel-dock-rail-footer", self.css)
        for locale in self.locales.values():
            self.assertNotIn("index.하단_고정_메모", locale)

    def test_dock_rail_order_is_persisted_per_side(self) -> None:
        self.assertIn('left: "supertory.dockRailOrder.left.v3"', self.js)
        self.assertIn('right: "supertory.dockRailOrder.right.v4"', self.js)
        self.assertIn("const DOCK_RAIL_DRAG_THRESHOLD = 6", self.js)
        self.assertIn("function restoreDockRailOrder(", self.js)
        self.assertIn("function persistDockRailOrder(", self.js)
        self.assertIn("function setupDockRailSorting(", self.js)
        restore_fn = self.js.split("function restoreDockRailOrder(", 1)[1].split(
            "function setupDockRailSorting(", 1
        )[0]
        self.assertIn("restored.splice(insertAt, 0, item)", restore_fn)
        self.assertIn("foundPrev", restore_fn)
        self.assertIn("htmlIndex + 1", restore_fn)
        self.assertNotIn("byId.forEach((item) => container.appendChild(item))", restore_fn)
        setup = self.js.split("function setupPanelDock()", 1)[1].split(
            "async function refreshAiStatus", 1
        )[0]
        self.assertLess(
            setup.find("restoreDockRailOrder(rail)"),
            setup.find('rail.addEventListener("click"'),
        )
        self.assertIn("suppressDockClickUntil", setup)
        self.assertIn(".panel-dock-item.is-dock-order-dragging", self.css)

    def test_widget_transparency_is_adjustable_and_persisted(self) -> None:
        self.assertIn('id="adminWidgetTransparency"', self.html)
        self.assertIn('id="adminWidgetTransparencyValue"', self.html)
        self.assertIn('id="adminWidgetGradient"', self.html)
        self.assertIn('id="adminWidgetGradientValue"', self.html)
        self.assertIn(
            'const WIDGET_TRANSPARENCY_STORAGE_KEY = "supertory.widgetTransparency"',
            self.js,
        )
        self.assertIn(
            'const WIDGET_GRADIENT_STORAGE_KEY = "supertory.widgetGradient"',
            self.js,
        )
        self.assertIn("function applyWidgetTransparency(", self.js)
        self.assertIn("function applyWidgetGradient(", self.js)
        self.assertIn("--widget-surface-opacity", self.js)
        self.assertIn("--widget-gradient-light", self.js)
        self.assertIn("--widget-gradient-accent", self.js)
        self.assertIn("setupWidgetTransparency();", self.js)
        dock_float = self.css.split(".idea-float.dock-float {", 1)[1].split("}", 1)[0]
        self.assertIn("linear-gradient(", dock_float)
        self.assertIn("var(--widget-gradient-light, 34%)", dock_float)
        self.assertIn("var(--widget-gradient-accent, 10%)", dock_float)
        self.assertIn("--widget-surface: color-mix(in srgb, var(--surface) 74%", dock_float)
        self.assertIn("var(--widget-surface, var(--surface))", dock_float)
        self.assertIn("var(--widget-surface-opacity-top, 92%)", dock_float)
        self.assertIn("var(--widget-surface-opacity-bottom, 82%)", dock_float)
        self.assertNotIn("backdrop-filter", dock_float)
        self.assertIn(".idea-float.dock-float.is-front", self.css)
        dock_header = self.css.split(
            ".idea-float.dock-float .idea-float-drag {", 1
        )[1].split("}", 1)[0]
        self.assertIn("--widget-gradient-header", dock_header)
        for locale in self.locales.values():
            self.assertIn("app.위젯_투명도", locale)
            self.assertIn("app.위젯_투명도_설명", locale)
            self.assertIn("app.투명도", locale)
            self.assertIn("app.그라데이션", locale)
            self.assertIn("app.위젯_그라데이션_강도", locale)

    def test_split_heading_omits_redundant_mode_labels(self) -> None:
        self.assertNotIn('class="split-scene-caption"', self.html)
        render = self.js.split("async function renderSplitViewer()", 1)[1].split(
            "function setupSplitEditMode()", 1
        )[0]
        setup = self.js.split("function setupSplitEditMode()", 1)[1].split(
            '  $("splitEditModeGroup")', 1
        )[0]
        self.assertNotIn("app.편집_가능", render)
        self.assertNotIn("app.읽기_전용", render)
        self.assertNotIn("app.편집_가능", setup)
        self.assertNotIn("app.읽기_전용", setup)

    def test_ideas_dock_reuses_idea_float_host(self) -> None:
        self.assertIn("function openDockFloat(itemId, sourceEl)", self.js)
        self.assertIn("function openDockFloatWindow(key, spec, sourceEl)", self.js)
        self.assertIn('ideas: {', self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0])
        self.assertIn("openDockFloatWindow(`dock:${itemId}`", self.js)
        self.assertIn("ideaFloatHost().appendChild(win)", self.js.split("function openDockFloatWindow", 1)[1])
        self.assertIn("ideaFloatWindows.set(key, win)", self.js)
        self.assertIn("bindIdeaFloatWindow(win, key", self.js)
        self.assertIn("if (!spec) return null;", self.js.split("function openDockFloat", 1)[1])
        self.assertIn("function setupPanelDock()", self.js)
        self.assertIn('safeSetup("setupPanelDock", setupPanelDock)', self.js)

    def test_dock_widgets_open_centered(self) -> None:
        self.assertIn("function dockFloatCenterPos(", self.js)
        self.assertIn("function dockFloatResolvedSide(", self.js)
        self.assertIn("function dockFloatSideLeft(", self.js)
        side_left = self.js.split("function dockFloatSideLeft(", 1)[1].split(
            "function dockFloatCenterPos(", 1
        )[0]
        self.assertIn('side === "left"', side_left)
        self.assertIn('side === "right"', side_left)
        self.assertIn('binderDockRail', side_left)
        self.assertIn('aiDockRail', side_left)
        self.assertIn("outlinePanel", side_left)
        self.assertIn("aiPanel", side_left)
        center = self.js.split("function dockFloatCenterPos(", 1)[1].split(
            "function dockFloatFallbackPos(", 1
        )[0]
        self.assertIn("dockFloatSideLeft(side, w)", center)
        self.assertIn("Math.round((vh - h) / 2)", center)
        fallback = self.js.split("function dockFloatFallbackPos(", 1)[1].split(
            "function dockAiFloatFallbackPos(", 1
        )[0]
        self.assertIn("dockFloatCenterPos(width, height, slot, dockFloatResolvedSide(side, sourceEl))", fallback)
        self.assertNotIn("getBoundingClientRect", fallback)
        ai_fallback = self.js.split("function dockAiFloatFallbackPos(", 1)[1].split(
            "function dockFloatBody(", 1
        )[0]
        self.assertIn('dockFloatFallbackPos("right", sourceEl, width, height, slot)', ai_fallback)
        open_fn = self.js.split("function openDockFloatWindow(key, spec, sourceEl)", 1)[1].split(
            "function openDockFloat(itemId, sourceEl)", 1
        )[0]
        self.assertIn(
            "dockFloatFallbackPos(spec.side, sourceEl, spec.defaultWidth, spec.defaultHeight)",
            open_fn,
        )

    def test_character_card_dock_widget(self) -> None:
        self.assertRegex(self.html, r'class="panel-dock-item is-ready"[^>]*data-dock-item="characters"')
        characters = self.html.split('data-dock-item="characters"', 1)[1].split("</button>", 1)[0]
        self.assertIn('path d="M15 13a3 3 0 1 0-6 0"', characters)
        self.assertIn("M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19", characters)
        self.assertIn('circle cx="12" cy="8" r="2"', characters)
        self.assertNotIn('path d="M16 2v2"', characters)
        self.assertNotIn('path d="M17.915 21a6 6 0 10-12 0"', characters)
        self.assertNotIn('path d="M16 10h2"', characters)
        self.assertNotIn('rect x="2" y="5" width="20" height="14" rx="2"', characters)
        settings_characters = self.html.split('data-settings-section="characters"', 1)[1].split("</section>", 1)[0]
        self.assertIn('path d="M15 13a3 3 0 1 0-6 0"', settings_characters)
        self.assertIn('circle cx="12" cy="8" r="2"', settings_characters)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0]
        self.assertIn("characters:", spec)
        self.assertIn("function openDockFloatWindow(key, spec, sourceEl)", self.js)
        self.assertIn("function dockFloatResizeConfig(spec)", self.js)
        self.assertIn("function openCharacterCardFloat(", self.js)
        self.assertIn("function setupDockCharacterNameClicks()", self.js)
        self.assertIn("bindIdeaFloatWindow(win, key, {", self.js)
        self.assertIn("resize: { minWidth: DOCK_CHAR_MIN_W, minHeight: DOCK_CHAR_MIN_H }", self.js)
        self.assertIn('windowClass: "dock-float-character"', self.js)
        self.assertIn("syncDockCharacterCardExpanded", self.js)
        self.assertIn('data-context-action="open-character-card"', self.html)
        self.assertIn("function characterAtTextOffset(", self.js)
        self.assertIn("function namedHitFromEditorPoint(", self.js)
        self.assertIn("function pointHitsRangeRects(", self.js)
        self.assertIn("function rangeFromEditorTextOffsets(", self.js)
        self.assertIn(
            "sceneCastLabels(character)",
            self.js.split("function characterAtTextOffset", 1)[1].split("function characterFromSelectedText", 1)[0],
        )
        char_from_point = self.js.split("function characterFromEditorPoint(", 1)[1].split("function itemAtTextOffset(", 1)[0]
        self.assertIn("namedHitFromEditorPoint", char_from_point)
        self.assertIn("pointHitsRangeRects", self.js.split("function namedHitFromEditorPoint(", 1)[1].split("function characterFromEditorPoint(", 1)[0])
        self.assertIn(".idea-float.dock-float.dock-float-character", self.css)
        self.assertIn(".idea-float.dock-float-character.is-expanded .dock-char-extra", self.css)
        ideas_spec = spec.split("ideas:", 1)[1].split("statsTracker:", 1)[0]
        self.assertIn("resize: false", ideas_spec)
        tracker_spec = spec.split("statsTracker:", 1)[1].split("characters:", 1)[0]
        self.assertIn("resize: false", tracker_spec)
        for locale in self.locales.values():
            self.assertIn("app.인물_카드", locale)
            self.assertIn("index.인물_카드_보기", locale)
            self.assertIn("index.인물_카드_힌트", locale)
            self.assertIn("index.연대기_보기", locale)
            self.assertIn("index.관계도_보기", locale)

    def test_world_card_dock_widget(self) -> None:
        self.assertRegex(self.html, r'class="panel-dock-item is-ready"[^>]*data-dock-item="world"')
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0]
        self.assertIn("world:", spec)
        self.assertIn("function openWorldCardFloat(", self.js)
        self.assertIn("function renderDockWorldBody(", self.js)
        self.assertIn("function paintDockWorldCard(", self.js)
        self.assertIn("function worldTermAtTextOffset(", self.js)
        self.assertIn("function worldTermFromEditorPoint(", self.js)
        click_fn = self.js.split("function onManuscriptCharacterNameClick(", 1)[1].split("function onManuscriptCharacterNameMove(", 1)[0]
        self.assertIn("characterFromEditorPoint", click_fn)
        self.assertIn("openCharacterCardFloat(charHit.id)", click_fn)
        self.assertIn("worldTermFromEditorPoint", click_fn)
        self.assertIn("openWorldCardFloat(worldHit.sectionId)", click_fn)
        self.assertLess(click_fn.find("openCharacterCardFloat"), click_fn.find("openWorldCardFloat"))
        self.assertIn("resize: { minWidth: DOCK_WORLD_MIN_W, minHeight: DOCK_WORLD_MIN_H }", self.js)
        self.assertIn('windowClass: "dock-float-world"', self.js)
        self.assertIn("syncDockWorldCardExpanded", self.js)
        self.assertIn('data-context-action="open-world-card"', self.html)
        self.assertIn("WORLD_SECTION_LEAD_FIELD", self.js)
        self.assertIn("where_when: \"locale\"", self.js)
        self.assertIn("unique_concept: \"special\"", self.js)
        self.assertIn("extreme_factor: \"extreme_event\"", self.js)
        self.assertIn("system_life: \"daily\"", self.js)
        self.assertIn("factions: \"factions\"", self.js)
        self.assertIn(".idea-float.dock-float.dock-float-world", self.css)
        self.assertIn(".idea-float.dock-float-world.is-expanded .dock-char-extra", self.css)
        self.assertIn("is-over-world-term", self.css)
        self.assertIn("function setupDockCharacterNameClicks()", self.js)
        for locale in self.locales.values():
            self.assertIn("index.세계관_카드", locale)
            self.assertIn("index.세계관_카드_보기", locale)
            self.assertIn("index.세계관_카드_힌트", locale)

    def test_item_card_dock_widget(self) -> None:
        self.assertRegex(self.html, r'class="panel-dock-item is-ready"[^>]*data-dock-item="items"')
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0]
        self.assertIn("items:", spec)
        items_spec = spec.split("items:", 1)[1].split("timeline:", 1)[0]
        self.assertIn("resize: false", items_spec)
        self.assertIn("function openItemCardFloat(", self.js)
        self.assertIn("function renderDockItemsBody(", self.js)
        self.assertIn("function paintDockItemCard(", self.js)
        self.assertIn("function itemAtTextOffset(", self.js)
        self.assertIn("function itemFromEditorPoint(", self.js)
        self.assertIn(
            "sceneCastLabels(item)",
            self.js.split("function itemAtTextOffset", 1)[1].split("function itemFromSelectedText", 1)[0],
        )
        click_fn = self.js.split("function onManuscriptCharacterNameClick(", 1)[1].split("function onManuscriptCharacterNameMove(", 1)[0]
        self.assertIn("characterFromEditorPoint", click_fn)
        self.assertIn("openCharacterCardFloat(charHit.id)", click_fn)
        self.assertIn("worldTermFromEditorPoint", click_fn)
        self.assertIn("openWorldCardFloat(worldHit.sectionId)", click_fn)
        self.assertIn("itemFromEditorPoint", click_fn)
        self.assertIn("openItemCardFloat(itemHit.id)", click_fn)
        self.assertLess(click_fn.find("openCharacterCardFloat"), click_fn.find("openWorldCardFloat"))
        self.assertLess(click_fn.find("openWorldCardFloat"), click_fn.find("openItemCardFloat"))
        self.assertIn("resize: { minWidth: DOCK_ITEM_MIN_W, minHeight: DOCK_ITEM_MIN_H }", self.js)
        self.assertIn('windowClass: "dock-float-item"', self.js)
        self.assertIn("syncDockItemCardExpanded", self.js)
        self.assertIn("openDockTimelineFloat(data.id, event.currentTarget, { kind: \"item\" })", self.js)
        self.assertNotIn("openDockRelationMinimapFloat", self.js.split("function paintDockItemCard(", 1)[1].split("function renderDockItemCard(", 1)[0])
        self.assertIn('data-context-action="open-item-card"', self.html)
        self.assertIn(".idea-float.dock-float.dock-float-item", self.css)
        self.assertIn(".idea-float.dock-float-item.is-expanded .dock-char-extra", self.css)
        self.assertIn("is-over-item-name", self.css)
        self.assertIn("function setupDockCharacterNameClicks()", self.js)
        for locale in self.locales.values():
            self.assertIn("index.아이템_카드", locale)
            self.assertIn("index.아이템_카드_보기", locale)
            self.assertIn("index.아이템_카드_힌트", locale)
            self.assertIn("index.아직_아이템이_없어요", locale)
            self.assertIn("index.연대기_아이템_필터", locale)

    def test_success_profile_dock_widget(self) -> None:
        self.assertRegex(
            self.html,
            r'class="panel-dock-item is-ready"[^>]*data-dock-item="successProfile"',
        )
        success_profile = self.html.split('data-dock-item="successProfile"', 1)[1].split("</button>", 1)[0]
        self.assertIn("M10 14.66V17a1 1 0 0 1-1 1 2 2 0 0 0-2 2v2", success_profile)
        self.assertIn("M6 9a6 6 0 0 0 12 0V3a1 1 0 0 0-1-1H7a1 1 0 0 0-1 1z", success_profile)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0]
        profile_spec = spec.split("successProfile:", 1)[1].split("manuscript:", 1)[0]
        self.assertIn('windowClass: "dock-float-success-profile"', profile_spec)
        self.assertIn(
            "resize: {\n      minWidth: DOCK_SUCCESS_PROFILE_MIN_W,\n"
            "      minHeight: DOCK_SUCCESS_PROFILE_MIN_H,",
            profile_spec,
        )
        self.assertIn("successProfile: DOCK_SUCCESS_PROFILE_KEY", self.js)
        self.assertIn("function renderDockSuccessProfileBody(", self.js)
        self.assertIn("function syncDockSuccessProfileFloat(", self.js)
        paint = self.js.split("function paintDockSuccessProfileBody(", 1)[1].split(
            "async function renderDockSuccessProfileBody(", 1
        )[0]
        for field in (
            "details.summary",
            "details.hook_style",
            "details.pacing_pattern",
            "details.dialogue_narration_balance",
            "details.style_signature",
            "profile?.analyzed_sections",
            "profile?.quantitative?.total_episodes",
        ):
            self.assertIn(field, paint)
        self.assertIn('data-role="dock-success-profile-select"', paint)
        self.assertIn("linkSuccessProfileToProject(nextId)", paint)
        self.assertIn("openDockSuccessFeedback", paint)
        self.assertIn("openDockSuccessAnalyst", paint)
        self.assertIn("openDockSuccessProfileSettings", paint)
        self.assertIn("openDockNewSuccessAnalysis", paint)
        self.assertIn('setAiModeValue("successfeedback")', self.js)
        self.assertIn('setToryChatMode("successAnalysis")', self.js)
        self.assertIn('openSettingsCollectionMain("successProfile")', self.js)
        self.assertIn('setAiModeValue("successpattern")', self.js)
        link = self.js.split("async function linkSuccessProfileToProject(", 1)[1].split(
            "/** Cache of all success profiles", 1
        )[0]
        self.assertIn("syncDockSuccessProfileFloat()", link)
        self.assertIn(".idea-float.dock-float.dock-float-success-profile", self.css)
        self.assertIn(".dock-success-profile-actions", self.css)
        for locale in self.locales.values():
            for key in (
                "app.흥행작_프로파일",
                "app.아직_연결된_흥행작_프로파일이_없어요",
                "app.다른_프로파일로_교체",
                "index.끝맺음_훅",
                "index.전개_패턴",
                "index.대사_지문_비중",
                "index.문체_특징",
                "index.분석_범위",
                "index.분석가와_대화",
                "index.전체_분석_보기",
                "index.새_분석",
            ):
                self.assertIn(key, locale)

    def test_timeline_dock_widget(self) -> None:
        self.assertIn('data-dock-item="timeline"', self.html)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0]
        self.assertIn("timeline:", spec)
        timeline_spec = spec.split("timeline:", 1)[1]
        self.assertIn("resize: { minWidth: DOCK_TIMELINE_MIN_W, minHeight: DOCK_TIMELINE_MIN_H }", timeline_spec)
        self.assertIn('windowClass: "dock-float-timeline"', timeline_spec)
        self.assertIn("function openDockTimelineFloat(", self.js)
        self.assertIn("function renderDockTimelineBody(", self.js)
        self.assertIn("/api/projects/${pid}/trait-history", self.js)
        self.assertIn("/api/items/${item.id}/trait-history", self.js)
        self.assertIn("renderTraitChronicleList(list, filteredDockTimelineEntries(), kind,", self.js)
        self.assertIn("openChronicleScene(", self.js.split("function renderTraitChronicleList", 1)[1].split("async function loadTraitChronicle", 1)[0])
        self.assertIn("showNames", self.js.split("function renderTraitChronicleList", 1)[1].split("async function loadTraitChronicle", 1)[0])
        self.assertIn("entry.character_name || entry.item_name", self.js.split("function renderTraitChronicleList", 1)[1].split("async function loadTraitChronicle", 1)[0])
        self.assertIn("data-role=\"dock-char-timeline\"", self.js)
        self.assertIn("openDockTimelineFloat(data.id", self.js)
        self.assertIn("if (itemId === \"timeline\") return openDockTimelineFloat(0, sourceEl);", self.js)
        self.assertIn('options.kind === "item"', self.js.split("function openDockTimelineFloat(", 1)[1].split("function dockBaitEpisodeLabel(", 1)[0])
        self.assertIn(".idea-float.dock-float.dock-float-timeline", self.css)
        self.assertIn(".dock-timeline-list", self.css)
        self.assertIn(".trait-chronicle-who", self.css)
        for locale in self.locales.values():
            self.assertIn("index.연대기", locale)
            self.assertIn("index.연대기_보기", locale)
            self.assertIn("index.연대기_인물_필터", locale)
            self.assertIn("index.연대기_아이템_필터", locale)
            self.assertIn("index.연대기_작품_안내", locale)
            self.assertIn("index.연대기_아이템_안내", locale)

    def test_relation_minimap_widget(self) -> None:
        self.assertNotIn('data-dock-item="relationMinimap"', self.html)
        self.assertNotIn("relationMinimap:", self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0])
        self.assertIn("function openDockRelationMinimapFloat(", self.js)
        self.assertIn("function neighborhoodRelationData(", self.js)
        self.assertIn("function renderDockRelationMinimapBody(", self.js)
        self.assertIn("function bindDockRelationMinimapStage(", self.js)
        self.assertIn("interactive: false", self.js.split("function paintDockRelationMinimap(", 1)[1].split("function fitDockRelationMinimap(", 1)[0])
        self.assertIn("showProfile: false", self.js.split("function paintDockRelationMinimap(", 1)[1].split("function fitDockRelationMinimap(", 1)[0])
        self.assertIn("persistRelationPositions([{ character_id: ch.id, x: ch.x, y: ch.y }])", self.js.split("function bindDockRelationMinimapStage(", 1)[1].split("async function loadDockRelationMinimapData(", 1)[0])
        self.assertNotIn("requestRelationSuggestions", self.js.split("function renderDockRelationMinimapBody(", 1)[1].split("function openDockRelationMinimapFloat(", 1)[0])
        self.assertNotIn("showRelationLabelModal", self.js.split("function renderDockRelationMinimapBody(", 1)[1].split("function openDockRelationMinimapFloat(", 1)[0])
        self.assertNotIn("relationSuggestButton", self.js.split("function renderDockRelationMinimapBody(", 1)[1].split("function openDockRelationMinimapFloat(", 1)[0])
        self.assertIn("openRelationCanvas({ characterId: dockRelationFocusId, fullscreen: true }", self.js)
        self.assertIn("data-role=\"dock-char-relations\"", self.js)
        self.assertIn("openDockRelationMinimapFloat(data.id", self.js)
        self.assertIn("resize: { minWidth: DOCK_RELATION_MIN_W, minHeight: DOCK_RELATION_MIN_H }", self.js.split("function openDockRelationMinimapFloat(", 1)[1].split("const win = openDockFloatWindow", 1)[0])
        self.assertIn(".idea-float.dock-float.dock-float-relation", self.css)
        self.assertIn(".dock-relation-stage", self.css)
        for locale in self.locales.values():
            self.assertIn("index.관계도_보기", locale)
            self.assertIn("index.관계도_미니맵", locale)
            self.assertIn("index.관계도_미니맵_안내", locale)
            self.assertIn("index.전체화면으로_보기", locale)
            self.assertIn("app.아직_등록된_관계가_없어요", locale)

    def test_settings_search_dock_widget(self) -> None:
        self.assertIn('data-dock-item="settingsSearch"', self.html)
        settings_search = self.html.split('data-dock-item="settingsSearch"', 1)[1].split("</button>", 1)[0]
        self.assertIn("M11 22H5.5a1 1 0 0 1 0-5h4.501", settings_search)
        self.assertIn("m21 22-1.879-1.878", settings_search)
        self.assertIn('circle cx="17" cy="18" r="3"', settings_search)
        self.assertNotIn("M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8", settings_search)
        self.assertNotIn('circle cx="11.5" cy="14.5" r="2.5"', settings_search)
        self.assertIn('id="settingsSearchLive"', self.html)
        self.assertIn('id="settingsSearchHome"', self.html)
        self.assertIn('id="settingsSearchInput"', self.html)
        self.assertIn('id="settingsSearchResults"', self.html)
        self.assertIn('data-context-action="cross-ref-search"', self.html)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0]
        self.assertIn("settingsSearch:", spec)
        self.assertIn("resize: { minWidth: DOCK_SETTINGS_SEARCH_MIN_W, minHeight: DOCK_SETTINGS_SEARCH_MIN_H }", spec)
        self.assertIn('windowClass: "dock-float-settings-search"', spec)
        self.assertIn("function openDockSettingsSearchFloat(", self.js)
        self.assertIn("function adoptSettingsSearchLive(", self.js)
        self.assertIn("function restoreSettingsSearchLive(", self.js)
        self.assertIn("function renderSettingsSearchResults(", self.js)
        self.assertIn("function openSettingsSearchHit(", self.js)
        self.assertIn("function openSettingsSearchFromSelection(", self.js)
        self.assertIn('if (itemId === "settingsSearch") return openDockSettingsSearchFloat("", sourceEl);', self.js)
        self.assertIn("openSettingsSearchFromSelection()", self.js)
        apply_fn = self.js.split("function applyDockSettingsSearchQuery(", 1)[1].split("function openDockSettingsSearchFloat(", 1)[0]
        self.assertIn("runSettingsSearch(next)", apply_fn)
        self.assertIn("renderSettingsSearchResults({}, \"\")", apply_fn)
        self.assertIn("function scheduleSettingsSearch(", self.js)
        self.assertIn(".idea-float.dock-float.dock-float-settings-search", self.css)
        self.assertIn(".settings-search-live", self.css)
        for locale in self.locales.values():
            self.assertIn("app.크로스_레퍼런스_시스템", locale)
            self.assertIn("index.크로스_레퍼런스로_검색", locale)
            self.assertIn("index.크로스_레퍼런스_검색_힌트", locale)

    def test_baits_dock_widget(self) -> None:
        self.assertRegex(self.html, r'class="panel-dock-item is-ready"[^>]*data-dock-item="baits"')
        baits = self.html.split('data-dock-item="baits"', 1)[1].split("</button>", 1)[0]
        self.assertIn('path d="M12 22v-9"', baits)
        self.assertIn("M15.17 2.21a1.67 1.67 0 0 1 1.63 0L21 4.57", baits)
        self.assertIn('title="떡밥 모음"', baits)
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        self.assertIn("baits:", spec)
        baits_spec = spec.split("baits:", 1)[1].split("settingsSearch:", 1)[0]
        self.assertIn("resize: { minWidth: DOCK_BAITS_MIN_W, minHeight: DOCK_BAITS_MIN_H }", baits_spec)
        self.assertIn('windowClass: "dock-float-baits"', baits_spec)
        self.assertIn("function renderDockBaitsBody(", self.js)
        self.assertIn("function loadDockBaits(", self.js)
        self.assertIn("function loadDockBaitsAndCollected(", self.js)
        self.assertIn("function setDockBaitResolved(", self.js)
        self.assertIn("/api/projects/${pid}/open-threads", self.js)
        self.assertIn("refreshBaitsFromServer()", self.js.split("function loadDockBaitsAndCollected(", 1)[1].split("function renderDockBaitsBody(", 1)[0])
        self.assertIn("method: \"PATCH\"", self.js.split("function setDockBaitResolved(", 1)[1].split("function loadDockBaitsAndCollected(", 1)[0])
        self.assertIn("baits: DOCK_BAITS_KEY", self.js)
        self.assertIn(".idea-float.dock-float.dock-float-baits", self.css)
        self.assertIn(".dock-bait-item.is-resolved", self.css)
        self.assertIn(".dock-baits-tabs", self.css)
        self.assertIn("data-settings-section=\"baits\"", self.html)
        self.assertIn("index.토리가_짚어둔_떡밥", self.js)
        self.assertIn("index.내가_모은_떡밥", self.js)
        self.assertIn('data-dock-baits-tab="tory"', self.js)
        self.assertIn('data-dock-baits-tab="collected"', self.js)
        self.assertIn('dockGuideTipHtml("dockBaits"', self.js)
        self.assertIn('id: "dockBaits"', self.js)
        for locale in self.locales.values():
            self.assertIn("index.열린_떡밥", locale)
            self.assertIn("index.열린_떡밥_안내", locale)
            self.assertIn("index.토리가_짚어둔_떡밥", locale)
            self.assertIn("index.내가_모은_떡밥", locale)
            self.assertIn("app.아직_열린_떡밥이_없어요", locale)
            self.assertIn("app.내가_모은_떡밥이_없어요", locale)
            self.assertIn("app.떡밥모음_위젯_안내", locale)
            self.assertIn("app.해결됨", locale)
            self.assertIn("app.본문_보기", locale)
            self.assertIn("app.토리_사전_안내", locale)
            self.assertIn("app.토리_사전_메인_안내", locale)
            self.assertIn("app.토리_사전에_추가_안내", locale)
            self.assertIn("app.등장_이력_안내", locale)

    def test_tory_vault_and_sources_dock_widgets(self) -> None:
        self.assertRegex(
            self.html,
            r'class="panel-dock-item is-ready"[^>]*data-dock-item="toryVault"',
        )
        self.assertRegex(
            self.html,
            r'class="panel-dock-item is-ready"[^>]*data-dock-item="sources"',
        )
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        vault_spec = spec.split("toryVault:", 1)[1].split("sources:", 1)[0]
        sources_spec = spec.split("sources:", 1)[1].split("credits:", 1)[0]
        self.assertIn('windowClass: "dock-float-tory-vault"', vault_spec)
        self.assertIn("resize: { minWidth: DOCK_TORY_VAULT_MIN_W, minHeight: DOCK_TORY_VAULT_MIN_H }", vault_spec)
        self.assertIn('windowClass: "dock-float-sources"', sources_spec)
        self.assertIn("resize: { minWidth: DOCK_SOURCES_MIN_W, minHeight: DOCK_SOURCES_MIN_H }", sources_spec)
        self.assertIn("toryVault: DOCK_TORY_VAULT_KEY", self.js)
        self.assertIn("sources: DOCK_SOURCES_KEY", self.js)
        self.assertIn("function renderDockToryVaultBody(", self.js)
        self.assertIn('dockGuideTipHtml("toryVault"', self.js)
        self.assertIn('{ id: "toryVault"', self.js)
        self.assertIn("function renderDockSourcesBody(", self.js)
        self.assertIn('dockGuideTipHtml("sources"', self.js)
        self.assertIn('{ id: "sources"', self.js)
        self.assertIn("function syncDockToryVaultFloat(", self.js)
        self.assertIn("function syncDockSourcesFloat(", self.js)
        self.assertIn("promptNewToryVaultNote({ openSettings: false })", self.js)
        self.assertIn("openSourceModal({})", self.js.split("function renderDockSourcesBody(", 1)[1].split("function dockManuscriptWin(", 1)[0])
        self.assertIn("handleToryVaultListClick", self.js.split("function renderDockToryVaultBody(", 1)[1].split("function syncDockSourcesFloat(", 1)[0])
        self.assertIn("handleSourceListClick", self.js.split("function renderDockSourcesBody(", 1)[1].split("function dockManuscriptWin(", 1)[0])
        self.assertIn(".idea-float.dock-float.dock-float-tory-vault", self.css)
        self.assertIn(".idea-float.dock-float.dock-float-sources", self.css)
        self.assertIn(".dock-tory-vault-list", self.css)
        self.assertIn(".dock-sources-list", self.css)
        for locale in self.locales.values():
            self.assertIn("app.토리의_수집창고", locale)
            self.assertIn("app.토리의_수집창고_안내", locale)
            self.assertIn("index.토리의_수집창고_안내", locale)
            self.assertIn("app.참고자료_출처", locale)
            self.assertIn("app.참고자료_출처_안내", locale)
            self.assertIn("index.참고자료_출처_안내", locale)
            self.assertIn("app.토리와_이야기하다_나온_아이디어를_수집하면", locale)
            self.assertIn("app.링크_출처_또는_PDF_Word_한글_텍스트", locale)

    def test_manuscript_dock_widget(self) -> None:
        self.assertRegex(self.html, r'class="panel-dock-item is-ready"[^>]*data-dock-item="manuscript"')
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        self.assertIn("manuscript:", spec)
        manuscript_spec = spec.split("manuscript:", 1)[1].split("settingsSearch:", 1)[0]
        self.assertIn("resize: { minWidth: DOCK_MANUSCRIPT_MIN_W, minHeight: DOCK_MANUSCRIPT_MIN_H }", manuscript_spec)
        self.assertIn('windowClass: "dock-float-manuscript"', manuscript_spec)
        self.assertIn("manuscript: DOCK_MANUSCRIPT_KEY", self.js)
        self.assertIn("function buildOutlineTreeHtml(", self.js)
        self.assertIn("function renderDockManuscriptBody(", self.js)
        self.assertIn("function bindDockManuscriptRoot(", self.js)
        self.assertIn("function paintDockManuscriptTree(", self.js)
        self.assertIn("function syncDockManuscriptFloat(", self.js)
        self.assertIn("buildOutlineTreeHtml({ readOnly: true })", self.js)
        self.assertIn("buildOutlineTreeHtml({ readOnly: false, chaptersArg })", self.js)
        paint_fn = self.js.split("function paintDockManuscriptTree(", 1)[1].split("function bindDockManuscriptRoot(", 1)[0]
        self.assertIn("readOnly: true", paint_fn)
        bind_fn = self.js.split("function bindDockManuscriptRoot(", 1)[1].split("function renderDockManuscriptBody(", 1)[0]
        self.assertIn("requestOpenScene(sceneId)", bind_fn)
        self.assertIn("toggleChapterExpanded", bind_fn)
        self.assertIn("togglePartExpanded", bind_fn)
        self.assertIn("toggleSceneExpanded", bind_fn)
        self.assertNotIn("startRenameScene", bind_fn)
        self.assertNotIn("createScene", bind_fn)
        self.assertNotIn("setupChapterDragAndDrop", bind_fn)
        self.assertNotIn("setupSceneNestDragAndDrop", bind_fn)
        self.assertNotIn("setupBinderContextMenu", bind_fn)
        self.assertNotIn("beginChapterRename", bind_fn)
        outline_fn = self.js.split("function renderOutline(chaptersArg)", 1)[1].split("async function beginChapterRename", 1)[0]
        self.assertIn("setupChapterDragAndDrop(outline)", outline_fn)
        self.assertIn("setupSceneNestDragAndDrop(outline)", outline_fn)
        self.assertIn("syncDockManuscriptFloat()", outline_fn)
        self.assertIn(".idea-float.dock-float.dock-float-manuscript", self.css)
        self.assertIn(".dock-manuscript-tree", self.css)
        for locale in self.locales.values():
            self.assertIn("index.목차보기", locale)
            self.assertIn("index.목차보기_안내", locale)

    def test_locale_keys_exist(self) -> None:
        keys = (
            "index.바인더_펼치기",
            "index.바인더_도크",
            "index.SuperTORY_펼치기",
            "index.SuperTORY_도크",
            "index.토리_1_1_대화창",
            "index.실시간_토리_체크",
            "index.크레딧_잔량",
            "index.내화면_보호",
            "index.내화면_보호_해제",
            "index.토리톡",
            "index.자주쓰는_가상독자_모음",
            "index.즐겨찾기한_가상독자가_없어요",
            "app.즐겨찾기는_최대_6명까지_등록할_수_있어요",
            "app.글자수_트래커",
            "index.남은_분량",
            "app.인물_카드",
            "index.인물_카드_보기",
            "index.인물_카드_힌트",
            "index.연대기",
            "index.연대기_보기",
            "index.연대기_인물_필터",
            "index.연대기_작품_안내",
            "index.관계도_보기",
            "index.관계도_미니맵",
            "index.전체화면으로_보기",
            "app.아직_등록된_관계가_없어요",
            "app.크로스_레퍼런스_시스템",
            "index.크로스_레퍼런스로_검색",
            "index.열린_떡밥",
            "index.토리가_짚어둔_떡밥",
            "index.내가_모은_떡밥",
            "app.아직_열린_떡밥이_없어요",
            "app.해결됨",
            "index.목차보기",
            "index.목차보기_안내",
            "index.세계관_카드",
            "index.세계관_카드_보기",
            "index.세계관_카드_힌트",
            "index.아이템_카드",
            "index.아이템_카드_보기",
            "index.아이템_카드_힌트",
            "index.아직_아이템이_없어요",
            "index.연대기_아이템_필터",
            "app.토리의_수집창고",
            "app.참고자료_출처",
        )
        for locale in self.locales.values():
            for key in keys:
                self.assertIn(key, locale)
        self.assertEqual(self.locales["ko"]["index.크레딧_잔량"], "도토리 잔량")
        self.assertEqual(self.locales["ko"]["index.크레딧_연동_준비중"], "도토리 연동은 준비 중이에요.")
        credits = self.html.split('data-dock-item="credits"', 1)[1].split("</button>", 1)[0]
        self.assertIn('title="도토리 잔량"', credits)
        self.assertIn("M12 4C8 4 4.5 6 4 8c-.243.97-.919 1.952-2 3", credits)
        character_chat = self.html.split('data-dock-item="characterChat"', 1)[1].split("</button>", 1)[0]
        self.assertIn("M16.051 12.616a1 1 0 0 1 1.909.024", character_chat)
        reader_chat = self.html.split('data-dock-item="readerChat"', 1)[1].split("</button>", 1)[0]
        self.assertIn("M22 5c0 9-4 12-6 12s-6-3-6-12c0-2 2-3 6-3s6 1 6 3", reader_chat)
        history = self.html.split('data-dock-item="aiHistory"', 1)[1].split("</button>", 1)[0]
        self.assertIn("M22 13a18.15 18.15 0 0 1-20 0", history)
        viewer = self.html.split('id="viewerModeButton"', 1)[1].split("</button>", 1)[0]
        self.assertIn('circle cx="6" cy="15" r="4"', viewer)
        self.assertIn('circle cx="18" cy="15" r="4"', viewer)
        self.assertIn('stroke="currentColor"', viewer)
        self.assertNotIn("#e0b48a", self.css)
        self.assertNotIn("#f0d0a8", self.css)
        self.assertNotIn("format-viewer-eye", self.html)

    def test_editor_view_zoom_control(self) -> None:
        self.assertIn('id="editorViewZoomButton"', self.html)
        self.assertIn('id="editorViewZoomMenu"', self.html)
        self.assertIn('id="manuscriptStatusBar"', self.html)
        bar = self.html.split('id="manuscriptStatusBar"', 1)[1].split('id="statsScopeSeg"', 1)[0]
        self.assertIn('id="editorViewZoomButton"', bar)
        self.assertIn("function setupEditorViewZoom(", self.js)
        self.assertIn("function applyEditorViewZoom(", self.js)
        self.assertIn("onEditorViewZoomWheel", self.js)
        self.assertIn('style.zoom', self.js)
        self.assertNotIn("formatSize", self.js.split("function applyEditorViewZoom(", 1)[1].split("function nudgeEditorViewZoom(", 1)[0])
        for locale in self.locales.values():
            for key in (
                "index.n_퍼센트",
                "index.화면_배율",
                "index.화면_배율_Ctrl_휠로_조절",
                "index.기타_줄임",
                "index.배율_퍼센트",
            ):
                self.assertIn(key, locale)
        self.assertEqual(self.locales["ko"]["index.n_퍼센트"], "${n}%")
        self.assertIn('M12 3v14', bar)
        self.assertIn('M5 10h14', bar)
        self.assertIn('M5 21h14', bar)

    def test_screen_protect_dock_widget(self) -> None:
        right_rail = self.html.split('id="aiDockRail"', 1)[1].split("</nav>", 1)[0]
        right_items = [
            item.split('"', 1)[0]
            for item in right_rail.split('data-dock-item="')[1:]
        ]
        self.assertEqual(right_items[-1], "screenProtect")
        self.assertEqual(right_items[-2], "credits")
        self.assertNotIn("priority", right_items)
        protect = right_rail.split('data-dock-item="screenProtect"', 1)[1].split("</button>", 1)[0]
        self.assertIn("M20 13c0 5-3.5 7.5-7.66 8.95", protect)
        self.assertIn("m4.243 5.21 14.39 12.472", protect)
        self.assertIn('stroke="currentColor"', protect)
        self.assertNotIn('width="24"', protect)
        overlay = self.html.split('id="screenProtectOverlay"', 1)[1].split("</div>", 1)[0]
        self.assertIn("M20 13c0 5-3.5 7.5-7.66 8.95", overlay)
        self.assertIn("m4.243 5.21 14.39 12.472", overlay)
        self.assertIn("function toggleScreenProtect(", self.js)
        self.assertIn("function setScreenProtectOn(", self.js)
        self.assertIn("function setupScreenProtect(", self.js)
        toggle_fn = self.js.split("function toggleDockFloat(", 1)[1].split(
            "function dockTrackerFallbackPos(", 1
        )[0]
        self.assertIn('itemId === "screenProtect"', toggle_fn)
        self.assertIn("toggleScreenProtect()", toggle_fn)
        self.assertNotIn("screenProtect", self.js.split("const AI_DOCK_PANEL_ITEMS = new Set([", 1)[1].split("]);", 1)[0])
        self.assertNotIn("screenProtect:", self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1].split("};", 1)[0])
        overlay_css = self.css.split(".screen-protect-overlay {", 1)[1].split("}", 1)[0]
        self.assertIn("position: fixed", overlay_css)
        self.assertIn("inset: 0", overlay_css)
        self.assertIn("backdrop-filter: blur(", overlay_css)
        self.assertIn("pointer-events: auto", overlay_css)
        self.assertIn("z-index: 400", overlay_css)
        icon_css = self.css.split(".screen-protect-overlay svg {", 1)[1].split("}", 1)[0]
        self.assertIn("width: 56px", icon_css)
        self.assertIn("height: 56px", icon_css)
        self.assertIn("setupScreenProtect();", self.js.split("function setupPanelDock()", 1)[1].split("async function refreshAiStatus", 1)[0])
        for locale in self.locales.values():
            self.assertIn("index.내화면_보호", locale)
            self.assertIn("index.내화면_보호_해제", locale)
        self.assertEqual(self.locales["ko"]["index.내화면_보호"], "내화면 보호")

    def test_manuscript_context_menu_fits_viewport(self) -> None:
        self.assertIn('id="desktopContextMenu"', self.html)
        self.assertIn("context-contrast-block", self.html)
        self.assertIn('data-i18n="index.고대비_모드"', self.html)
        self.assertIn("#desktopContextMenu {", self.css)
        desktop_css = self.css.split("#desktopContextMenu {", 1)[1].split("#desktopContextMenu ", 1)[0]
        self.assertIn("max-height: calc(100vh - 16px)", desktop_css)
        self.assertIn("overflow-y: auto", desktop_css)
        self.assertIn(".context-contrast-block {", self.css)
        show_fn = self.js.split("function showDesktopContextMenu(", 1)[1].split(
            "function captureManuscriptRangeFromEvent(", 1
        )[0]
        self.assertIn("menu.style.maxHeight", show_fn)
        self.assertIn("window.innerHeight - pad * 2", show_fn)
        pos_fn = self.js.split("function positionContextMenu(", 1)[1].split(
            "function updateFolderContextToggleLabels(", 1
        )[0]
        self.assertIn("menu.style.maxHeight", pos_fn)


if __name__ == "__main__":
    unittest.main()
