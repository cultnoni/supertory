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
        self.assertIn("returnToManuscriptFromSettingsMain", close_fn)
        self.assertNotIn("openCharacterBoard", close_fn)
        open_fn = self.js.split("async function openCharacter(characterId)", 1)[1].split(
            "async function saveCharacter", 1
        )[0]
        self.assertIn("flushCharacterAutoSave", open_fn)
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


if __name__ == "__main__":
    unittest.main()
