"""설정집(세계관·캐릭터·메모 등) 자동 저장 계약."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SettingsCodexAutosaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    def test_character_editor_status_and_save_hint(self) -> None:
        self.assertIn('id="characterInfo"', self.html)
        self.assertIn('id="characterEditor"', self.html)
        self.assertIn("index.저장_자동_저장됨", self.html)
        self.assertIn('id="characterName"', self.html)
        self.assertIn('id="characterProfile"', self.html)

    def test_character_autosave_helpers(self) -> None:
        self.assertIn("function persistCharacter", self.js)
        self.assertIn("function flushCharacterAutoSave", self.js)
        self.assertIn("function setupCharacterEditorAutosave", self.js)
        self.assertIn("function markCharacterDirty", self.js)
        self.assertIn("setupCharacterEditorAutosave", self.js)

    def test_character_fields_bind_autosave(self) -> None:
        ids = self.js.split("const CHARACTER_EDITOR_FIELD_IDS", 1)[1].split(";", 1)[0]
        for field_id in (
            "characterName",
            "characterSortName",
            "characterRole",
            "characterSummary",
            "characterProfile",
            "characterStrengths",
            "characterWeaknesses",
            "characterNotes",
        ):
            self.assertIn(field_id, ids)
        setup = self.js.split("function setupCharacterEditorAutosave", 1)[1].split(
            "async function openCharacter", 1
        )[0]
        self.assertIn("CHARACTER_EDITOR_FIELD_IDS", setup)
        self.assertIn("markCharacterDirty", setup)
        self.assertIn("persistCharacter", setup)

    def test_close_and_switch_character_flushes(self) -> None:
        close_fn = self.js.split("async function closeCharacterEditor", 1)[1].split(
            "function bindSettingsDocSidebarInput", 1
        )[0]
        self.assertIn("flushCharacterAutoSave", close_fn)
        self.assertIn("openCharacterBoard", close_fn)
        self.assertIn("openRelationCanvas", close_fn)
        self.assertIn("returnToManuscriptFromSettingsMain", close_fn)
        self.assertIn("characterEditorReturnTo", close_fn)
        open_fn = self.js.split("async function openCharacter(characterId", 1)[1].split(
            "async function saveCharacter", 1
        )[0]
        self.assertIn("flushCharacterAutoSave", open_fn)
        self.assertIn("characterEditorReturnTo", open_fn)
        self.assertNotIn("state.sceneId = null", open_fn)

    def test_settings_main_close_returns_to_manuscript(self) -> None:
        self.assertIn("async function returnToManuscriptFromSettingsMain", self.js)
        self.assertIn("keepBinder: true", self.js)
        board_close = self.js.split("async function closeCharacterBoard", 1)[1]
        board_close = board_close.split("\n}", 1)[0]
        self.assertIn("returnToManuscriptFromSettingsMain", board_close)
        self.assertNotIn("openCharacter(", board_close)
        synopsis_close = self.js.split("async function closeSynopsisMain", 1)[1].split(
            "function applySettingsDocChrome", 1
        )[0]
        self.assertIn("returnToManuscriptFromSettingsMain", synopsis_close)
        self.assertNotIn("openCharacter(", synopsis_close)
        idea_close = self.js.split("async function closeIdeaBoard", 1)[1].split(
            "function openIdeaBoard", 1
        )[0]
        self.assertIn("returnToManuscriptFromSettingsMain", idea_close)
        self.assertNotIn("openCharacter(", idea_close)
        keyword_close = self.js.split("function closeKeywordBoard", 1)[1].split(
            "function placeGenreBlockForKeywordBoard", 1
        )[0]
        self.assertIn("returnToManuscriptFromSettingsMain", keyword_close)
        collection_close = self.js.split("function closeSettingsCollectionMain", 1)[1].split(
            "function openSettingsCollectionMain", 1
        )[0]
        self.assertIn("returnToManuscriptFromSettingsMain", collection_close)
        self.assertNotIn("openCharacter(", collection_close)
        board_open = self.js.split("async function openCharacterBoard()", 1)[1].split(
            "async function closeCharacterBoard", 1
        )[0]
        self.assertNotIn("state.sceneId = null", board_open)

    def test_main_view_collapses_settings_section(self) -> None:
        self.assertIn("function collapseSettingsSectionAfterMainOpen", self.js)
        self.assertIn("function isAnySettingsMainViewOpen", self.js)
        helper = self.js.split("function collapseSettingsSectionAfterMainOpen(", 1)[1].split(
            "function setOpenSettingsSection(", 1
        )[0]
        self.assertIn("state.openSettingsSection = null", helper)
        self.assertIn("applySettingsSectionState", helper)
        setup = self.js.split("function setupSettingsCodex(", 1)[1].split(
            "document.querySelectorAll(\"[data-save-settings-doc]\"", 1
        )[0]
        self.assertIn("collapseSettingsSectionAfterMainOpen", setup)
        self.assertIn("isAnySettingsMainViewOpen", setup)

    def test_intro_logsyn_main_open_does_not_flash_panel(self) -> None:
        """Header → must not expand the accordion before the async main open."""
        meta = self.js.split("const SETTINGS_BOOKMARK_META = {", 1)[1].split(
            "const READING_INVITE_SEEN_PREFIX", 1
        )[0]
        intro = meta.split("intro:", 1)[1].split("intent:", 1)[0]
        logsyn = meta.split("logsyn:", 1)[1].split("keywords:", 1)[0]
        self.assertIn('open: () => openSettingsDocMain("intro")', intro)
        self.assertIn('open: () => openSettingsDocMain("synopsis")', logsyn)
        self.assertNotIn("applySettingsSectionState", intro)
        self.assertNotIn("applySettingsSectionState", logsyn)
        self.assertNotIn("state.openSettingsSection", intro)
        self.assertNotIn("state.openSettingsSection", logsyn)

    def test_codex_flush_on_hide(self) -> None:
        self.assertIn("function flushPendingCodexSaves", self.js)
        scene_setup = self.js.split("function setupSceneAutoSave", 1)[1]
        vis = scene_setup.split('document.addEventListener("visibilitychange"', 1)[1].split(
            "window.addEventListener(\"online\"", 1
        )[0]
        self.assertIn("flushPendingCodexSaves", vis)
        pagehide = scene_setup.split('window.addEventListener("pagehide"', 1)[1].split(
            "function ", 1
        )[0]
        self.assertIn("flushPendingCodexSaves", pagehide)

    def test_world_and_sidebar_blur_persist(self) -> None:
        world = self.js.split("function bindWorldBuildingFormRoot", 1)[1].split(
            "function bindWorldBuildingFormInputs", 1
        )[0]
        self.assertIn('persistSettingsDoc("world"', world)
        sidebar = self.js.split("function bindSettingsDocSidebarInput", 1)[1].split(
            "function bindWorldBuildingFormRoot", 1
        )[0]
        self.assertIn("persistSettingsDoc(kind", sidebar)

    def test_idea_card_autosave(self) -> None:
        self.assertIn("function scheduleIdeaCardAutoSave", self.js)
        self.assertIn("function flushIdeaCardAutosaves", self.js)
        bind = self.js.split("function bindIdeaCardEditors", 1)[1].split(
            "function renderIdeaBoard", 1
        )[0]
        self.assertIn("scheduleIdeaCardAutoSave", bind)
        self.assertIn("flushIdeaCard", bind)
        board = self.js.split("function renderIdeaBoard", 1)[1].split(
            "function applyIdeaCardColor", 1
        )[0]
        self.assertIn("bindIdeaCardEditors(card)", board)

    def test_character_aliases_are_per_character(self) -> None:
        self.assertIn("function currentCharacterAliasNames", self.js)
        self.assertIn("function renderAliasList", self.js)
        self.assertIn('aliases: currentCharacterAliasNames()', self.js)
        open_fn = self.js.split("async function openCharacter", 1)[1].split(
            "async function saveCharacter", 1
        )[0]
        self.assertIn('$("newAlias").value = ""', open_fn)
        self.assertIn("renderAliasList", open_fn)
        setup = self.js.split("function setupCharacterEditorAutosave", 1)[1].split(
            "async function openCharacter", 1
        )[0]
        self.assertIn("newAlias", setup)
        self.assertIn("commitAliasInputToState", setup)
        add_fn = self.js.split("async function addAlias", 1)[1].split("function allScenes", 1)[0]
        self.assertIn("persistCharacter", add_fn)
        self.assertNotIn("openCharacter(state.characterId)", add_fn)

    def test_project_switch_uses_load_generation(self) -> None:
        self.assertIn("function bumpProjectLoadGen", self.js)
        self.assertIn("function isCurrentProjectLoadGen", self.js)
        self.assertIn("function isAbortError", self.js)
        self.assertIn("const switchGen = bumpProjectLoadGen()", self.js)
        self.assertIn("if (!isCurrentProjectLoadGen(switchGen)) return;", self.js)
        load = self.js.split("async function loadProject()", 1)[1].split(
            "function previewLines", 1
        )[0]
        self.assertIn("projectLoadController?.signal", load)
        self.assertIn("if (isAbortError(error) || !stillCurrent()) return;", load)
        persist = self.js.split("async function persistSettingsDoc", 1)[1].split(
            "async function persistSynopsis", 1
        )[0]
        self.assertIn("liveProjectId(options.projectId ?? state.projectId)", persist)
        self.assertIn("`/api/projects/${projectId}/settings`", persist)
        genre = self.js.split("async function persistProjectGenre", 1)[1].split(
            "function syncGenreDisplayButtons", 1
        )[0]
        self.assertIn("`/api/projects/${projectId}/settings`", genre)
        keywords = self.js.split("async function persistProjectKeywords", 1)[1].split(
            "function addProjectKeyword", 1
        )[0]
        self.assertIn("`/api/projects/${projectId}/settings`", keywords)
        flush = self.js.split("async function flushPendingCodexSaves()", 1)[1].split(
            "function hideSynopsisMain", 1
        )[0]
        self.assertIn("persistProjectGenre({ quiet: true, projectId })", flush)
        self.assertIn("persistProjectKeywords({ quiet: true, projectId })", flush)

    def test_intro_intent_dismissible_guide_and_no_idle_editing_status(self) -> None:
        self.assertIn('id="settingsDocIntroTipMount"', self.html)
        self.assertIn('data-guide-tip="introIntent"', self.html)
        self.assertIn('data-guide-tip-dismiss="introIntent"', self.html)
        self.assertIn('{ id: "introIntent"', self.js)
        chrome = self.js.split("function applySettingsDocChrome(", 1)[1].split(
            "async function openSettingsDocMain", 1
        )[0]
        self.assertIn('kind === "intro" || kind === "intent"', chrome)
        self.assertIn("settingsDocIntroTipMount", chrome)
        self.assertIn("function settingsDocIdleSaveStatus(", self.js)
        idle = self.js.split("function settingsDocIdleSaveStatus(", 1)[1].split(
            "function markSynopsisDirty", 1
        )[0]
        self.assertIn('kind === "intro" || kind === "intent"', idle)
        self.assertIn('kind === "logline" || kind === "synopsis"', idle)
        self.assertIn('return ""', idle)
        open_fn = self.js.split("async function openSettingsDocMain", 1)[1].split(
            "async function openSynopsisMain", 1
        )[0]
        self.assertIn("setSynopsisSaveStatus(settingsDocIdleSaveStatus(kind))", open_fn)

    def test_logsyn_main_layout_and_tori_fill(self) -> None:
        self.assertIn('id="settingsDocOutlineBlock"', self.html)
        self.assertIn('id="settingsDocOutlineMain"', self.html)
        self.assertIn('id="toriFillLogsynButton"', self.html)
        self.assertIn('id="logsynToriFillModal"', self.html)
        self.assertIn('data-guide-tip="loglineHint"', self.html)
        self.assertIn('data-guide-tip="outlineSummaryHint"', self.html)
        self.assertIn('data-guide-tip="synopsisHint"', self.html)
        self.assertIn('id="logsynToriWantLogline"', self.html)
        self.assertIn('id="logsynToriWantSynopsis"', self.html)
        self.assertIn('id="logsynToriKisung"', self.html)
        self.assertIn('id="logsynToriFlow"', self.html)
        self.assertIn('id="logsynToriLimitInput"', self.html)
        self.assertIn('id="logsynToriRegenerateButton"', self.html)
        self.assertIn("function setupLogsynToriFillUi(", self.js)
        self.assertIn("function runLogsynToriFill(", self.js)
        self.assertIn("settingsDocCanToriOverwrite", self.js)
        self.assertIn("wrapToriDraftHtml", self.js)
        chrome = self.js.split("function applySettingsDocChrome(", 1)[1].split(
            "async function openSettingsDocMain", 1
        )[0]
        self.assertIn("settingsDocOutlineBlock", chrome)
        self.assertIn("toriFillLogsynButton", chrome)
        self.assertIn("is-logsyn", chrome)
        self.assertIn("isIntroPair", chrome)
        self.assertIn("showFill", chrome)
        self.assertIn('id="logsynToriWantIntro"', self.html)
        self.assertIn('id="logsynToriWantIntent"', self.html)
        self.assertIn("function buildIntroToriPrompt(", self.js)
        self.assertIn("function runIntroToriFill(", self.js)
        self.assertIn('logsynToriFillMode === "intro"', self.js)
        logsyn_body = self.html.split('id="logsynBody"', 1)[1].split(
            'data-settings-section="keywords"', 1
        )[0]
        self.assertNotIn("guide-tip-box", logsyn_body)
        self.assertNotIn("class=\"hint\"", logsyn_body)
        self.assertIn('id="projectLogline"', logsyn_body)
        self.assertIn('id="projectOutlineSummary"', logsyn_body)
        self.assertIn('id="projectSynopsis"', logsyn_body)

    def test_keyword_board_dismissible_guide_and_click_genre_picker(self) -> None:
        self.assertIn('data-guide-tip="keywordBoard"', self.html)
        self.assertIn('data-guide-tip-dismiss="keywordBoard"', self.html)
        self.assertIn('id="keywordBoardHint"', self.html)
        self.assertIn('{ id: "keywordBoard"', self.js)
        self.assertIn("function toggleGenreContextMenu(", self.js)
        self.assertIn("function togglePurposeContextMenu(", self.js)
        self.assertIn("function placePickerMenuBelowAnchor(", self.js)
        keywords_body = self.html.split('id="keywordsBody"', 1)[1].split(
            'data-settings-section=', 1
        )[0]
        self.assertNotIn("우클릭", keywords_body)
        self.assertNotIn('id="keywordsHint"', keywords_body)
        self.assertNotIn("태그를 고르면 이 작품 키워드로", keywords_body)
        self.assertIn('id="settingsGenreSummary"', keywords_body)
        self.assertNotIn('id="settingsGenreBlock"', keywords_body)
        self.assertNotIn('id="purposeDisplay"', keywords_body)
        self.assertIn("index.작품_종류와_메인_하위_장르를_정해요_19금", self.html)
        self.assertIn("PURPOSE_OPTION_KEYS", self.js)
        self.assertIn("GENRE_LITERATURE_MAIN_GENRES", self.js)
        self.assertIn("WEB_NOVEL_DETAIL_GENRES", self.js)
        board = self.html.split('id="keywordBoard"', 1)[1].split(
            'id="settingsCollectionBoard"', 1
        )[0]
        self.assertIn('id="settingsGenreBlock"', board)
        self.assertIn('id="purposeDisplay"', board)
        self.assertIn("guide-tip-box", board)
        genre_mount_at = board.find('id="keywordBoardGenreMount"')
        hint_at = board.find('id="keywordBoardHint"')
        self.assertGreater(genre_mount_at, -1)
        self.assertGreater(hint_at, genre_mount_at)
        bind = self.js.split("function setupGenrePicker()", 1)[1].split(
            "function projectOptionLabel", 1
        )[0]
        self.assertIn("toggleGenreContextMenu(kind, btn)", bind)
        self.assertNotIn("event.clientX, event.clientY", bind)

    def test_world_character_item_dictionary_codex_ui(self) -> None:
        world_body = self.html.split('id="worldBody"', 1)[1].split(
            'data-settings-section="characters"', 1
        )[0]
        self.assertNotIn("아래 다섯 칸에 맞춰", world_body)
        self.assertNotIn("guide-tip-box", world_body)
        self.assertIn('data-world-section="geography"', world_body)
        self.assertIn('data-world-field="geo_terrain"', world_body)
        self.assertIn("data-world-add-extra", world_body)
        self.assertIn('data-world-extras', world_body)
        workspace = self.html.split('id="worldbuildingWorkspace"', 1)[1].split(
            'id="characterBoard"', 1
        )[0]
        self.assertIn('data-guide-tip="worldHint"', workspace)
        self.assertIn('data-guide-tip-dismiss="worldHint"', workspace)
        title_at = workspace.find("worldbuilding-main-title")
        tip_at = workspace.find('data-guide-tip="worldHint"')
        self.assertGreater(title_at, -1)
        self.assertGreater(tip_at, title_at)
        self.assertIn('{ id: "worldHint"', self.js)
        self.assertIn("function addWorldExtraSection(", self.js)
        characters_body = self.html.split('id="charactersBody"', 1)[1].split(
            'data-settings-section="items"', 1
        )[0]
        self.assertNotIn("인물을 눌러 설정을 수정하거나", characters_body)
        items_body = self.html.split('id="itemsBody"', 1)[1].split(
            'data-settings-section="dictionary"', 1
        )[0]
        self.assertNotIn("아이템을 눌러 설정을 수정하거나", items_body)
        self.assertIn("settings-panel-card-grid", items_body)
        dictionary_body = self.html.split('id="dictionaryBody"', 1)[1].split(
            'data-settings-section="baits"', 1
        )[0]
        self.assertNotIn("단어를 눌러 뜻을 수정하거나", dictionary_body)
        self.assertIn("index.plus_단어", dictionary_body)
        self.assertIn("settings-panel-card-grid", dictionary_body)
        self.assertIn("dictionary-panel-filters", dictionary_body)
        plus_at = dictionary_body.find("newDictionaryButton")
        search_at = dictionary_body.find('id="dictionarySearch"')
        self.assertGreater(plus_at, -1)
        self.assertGreater(search_at, plus_at)
        self.assertIn('data-i18n="index.plus_단어"', self.html)
        self.assertIn('data-i18n="index.새단어"', self.html)
        self.assertIn("openDictionaryModal()", self.js)
        characters_nav = characters_body
        self.assertIn("settings-panel-card-grid", characters_nav)
        self.assertIn('data-guide-tip="characterBoard"', self.html)
        self.assertIn('data-guide-tip-dismiss="characterBoard"', self.html)
        editor = self.html.split('id="characterEditor"', 1)[1].split(
            'id="itemEditor"', 1
        )[0]
        self.assertNotIn("인물 설정을 가운데에서 자세히 편집합니다", editor)
        vault_body = self.html.split('id="toryVaultBody"', 1)[1].split(
            'data-settings-section="readingInvite"', 1
        )[0]
        self.assertIn("newToryVaultButton", vault_body)
        self.assertIn("clearToryVaultButton", vault_body)
        self.assertIn("#toryVaultBody > .settings-box-toolbar", self.css)
        invite_body = self.html.split('id="readingInviteBody"', 1)[1].split(
            'data-settings-section="sources"', 1
        )[0]
        self.assertIn('data-guide-tip="readingInvite"', invite_body)
        self.assertIn("readingInviteCreateBlock", invite_body)
        self.assertIn("만든 링크 관리", invite_body)
        self.assertIn("settings-box-body #readingInviteCreateBlock", self.css)
        self.assertIn("function restoreReadingInvite(", self.js)
        self.assertIn("data-toggle-invite-active", self.js)


if __name__ == "__main__":
    unittest.main()
