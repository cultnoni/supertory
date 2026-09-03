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
        self.assertIn('data-dock-item="tools"', self.html)
        self.assertNotIn('data-dock-item="intro"', self.html)
        self.assertNotIn('data-dock-item="logsyn"', self.html)
        self.assertNotIn('data-dock-item="keywords"', self.html)
        self.assertNotIn('data-dock-item="readingInvite"', self.html)
        self.assertIn('data-settings-section="readingInvite"', self.html)
        self.assertIn('data-settings-main="readingInvite"', self.html)

    def test_dock_rail_toggle_and_active_state(self) -> None:
        timeline = self.html.split('data-dock-item="timeline"', 1)[1].split("</button>", 1)[0]
        self.assertIn('<circle cx="12" cy="12" r="7.25"/>', timeline)
        self.assertIn("M12 8.4v4.1l2.9 1.7", timeline)
        self.assertNotIn("M8 5.5v13", timeline)
        self.assertIn("function toggleDockFloat(", self.js)
        self.assertIn("function syncDockRailButtons(", self.js)
        self.assertIn("function isDockRailItemActive(", self.js)
        self.assertIn("toggleDockFloat(item.dataset.dockItem, item)", self.js)
        self.assertNotIn("openDockFloat(item.dataset.dockItem, item)", self.js.split("function setupPanelDock", 1)[1].split("function refreshAiStatus", 1)[0])
        self.assertIn("expandBinderPanelButton", self.js)
        self.assertIn("setBinderPanelOpen(true)", self.js)
        toggle_fn = self.js.split("function toggleDockFloat(", 1)[1].split("function dockTrackerFallbackPos(", 1)[0]
        self.assertIn("closeIdeaFloat(key)", toggle_fn)
        self.assertIn("return openDockFloat(itemId, sourceEl)", toggle_fn)
        active_fn = self.js.split("function isDockRailItemActive(", 1)[1].split("function syncDockRailButtons(", 1)[0]
        self.assertIn("itemId === \"characters\"", active_fn)
        self.assertIn("DOCK_CHAR_KEY_PREFIX", active_fn)
        self.assertIn("btn.classList.toggle(\"is-open\", on)", self.js.split("function syncDockRailButtons(", 1)[1].split("function syncDockStatsTrackerButton(", 1)[0])
        self.assertIn("if (id === DOCK_STATS_TRACKER_KEY) setDockTrackerOpenPref(false)", self.js)
        self.assertIn(".panel-dock-item.is-open", self.css)
        self.assertIn(".panel-dock-item svg", self.css)
        self.assertIn("overflow: visible", self.css.split(".panel-dock-item svg {", 1)[1].split("}", 1)[0])

    def test_stats_tracker_is_pinned_dock_widget(self) -> None:
        self.assertIn('data-dock-item="statsTracker"', self.html)
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
        self.assertEqual(self.locales["ko"]["app.글자_공포"], "공포")
        self.assertEqual(self.locales["ko"]["app.글자_공제"], "공제")
        self.assertIn(".dock-stats-flags", self.css)

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

    def test_character_card_dock_widget(self) -> None:
        self.assertRegex(self.html, r'class="panel-dock-item is-ready"[^>]*data-dock-item="characters"')
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
        self.assertIn(
            "sceneCastLabels(character)",
            self.js.split("function characterAtTextOffset", 1)[1].split("function characterFromSelectedText", 1)[0],
        )
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
        spec = self.js.split("const DOCK_FLOAT_SPECS = {", 1)[1]
        self.assertIn("baits:", spec)
        baits_spec = spec.split("baits:", 1)[1].split("settingsSearch:", 1)[0]
        self.assertIn("resize: { minWidth: DOCK_BAITS_MIN_W, minHeight: DOCK_BAITS_MIN_H }", baits_spec)
        self.assertIn('windowClass: "dock-float-baits"', baits_spec)
        self.assertIn("function renderDockBaitsBody(", self.js)
        self.assertIn("function loadDockBaits(", self.js)
        self.assertIn("function setDockBaitResolved(", self.js)
        self.assertIn("/api/projects/${pid}/open-threads", self.js)
        self.assertIn("method: \"PATCH\"", self.js.split("function setDockBaitResolved(", 1)[1].split("function renderDockBaitsBody(", 1)[0])
        self.assertIn("baits: DOCK_BAITS_KEY", self.js)
        self.assertIn(".idea-float.dock-float.dock-float-baits", self.css)
        self.assertIn(".dock-bait-item.is-resolved", self.css)
        self.assertIn("data-settings-section=\"baits\"", self.html)
        for locale in self.locales.values():
            self.assertIn("index.열린_떡밥", locale)
            self.assertIn("index.열린_떡밥_안내", locale)
            self.assertIn("app.아직_열린_떡밥이_없어요", locale)
            self.assertIn("app.해결됨", locale)
            self.assertIn("app.본문_보기", locale)

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
        self.assertIn("openScene(sceneId)", bind_fn)
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
        )
        for locale in self.locales.values():
            for key in keys:
                self.assertIn(key, locale)


if __name__ == "__main__":
    unittest.main()
