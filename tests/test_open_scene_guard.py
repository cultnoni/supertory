"""Guards so openScene cannot paint a slower, foreign, or stale manuscript."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "web" / "app.js"
KO_PATH = ROOT / "web" / "locales" / "ko.json"
EN_PATH = ROOT / "web" / "locales" / "en.json"
ES_PATH = ROOT / "web" / "locales" / "es.json"


def _slice_function(source: str, name: str) -> str:
    needle = f"function {name}("
    start = source.find(needle)
    if start < 0:
        needle = f"async function {name}("
        start = source.find(needle)
    if start < 0:
        raise AssertionError(f"missing function {name}")
    nxt = source.find("\nfunction ", start + len(needle))
    nxt_async = source.find("\nasync function ", start + len(needle))
    cuts = [i for i in (nxt, nxt_async) if i >= 0]
    end = min(cuts) if cuts else None
    return source[start:end]


class OpenSceneGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = JS_PATH.read_text(encoding="utf-8")
        cls.ko = json.loads(KO_PATH.read_text(encoding="utf-8"))
        cls.en = json.loads(EN_PATH.read_text(encoding="utf-8"))
        cls.es = json.loads(ES_PATH.read_text(encoding="utf-8"))

    def test_scene_open_uses_load_generation(self) -> None:
        self.assertIn("function bumpSceneOpenGen", self.js)
        self.assertIn("function isCurrentSceneOpenGen", self.js)
        self.assertIn("bumpSceneOpenGen()", self.js)
        open_fn = _slice_function(self.js, "openScene")
        self.assertIn("const gen = bumpSceneOpenGen()", open_fn)
        self.assertIn("sceneOpenController?.signal", open_fn)
        self.assertIn("if (!stillThisOpen()) return", open_fn)
        self.assertIn("fillSceneEditorFields(detail)", open_fn)
        fill_at = open_fn.find("fillSceneEditorFields(detail)")
        gen_at = open_fn.find("const gen = bumpSceneOpenGen()")
        still_before_fill = open_fn.rfind("if (!stillThisOpen()) return", 0, fill_at)
        self.assertGreater(gen_at, 0)
        self.assertGreater(still_before_fill, gen_at)
        self.assertGreater(fill_at, still_before_fill)
        self.assertIn("`/api/scenes/${nextId}`", open_fn)
        self.assertIn("apiOpts", open_fn)

    def test_project_switch_aborts_in_flight_scene_open(self) -> None:
        bump = _slice_function(self.js, "bumpProjectLoadGen")
        self.assertIn("bumpSceneOpenGen()", bump)

    def test_open_scene_rejects_foreign_project_id(self) -> None:
        open_fn = _slice_function(self.js, "openScene")
        self.assertIn("scenePayloadProjectId(detail)", open_fn)
        self.assertIn("detailProjectId !== currentProjectId", open_fn)
        self.assertIn("rejectForeignSceneOpen(nextId, detail, gen)", open_fn)
        reject = _slice_function(self.js, "rejectForeignSceneOpen")
        self.assertIn("dropStaleEpisodeTab(sceneId)", reject)
        self.assertIn("toastForeignOrMissingScene(detail)", reject)

    def test_foreign_scene_copy_is_clearer_than_novel_missing(self) -> None:
        missing = "app.이_회차가_속한_작품을_찾을_수_없어요_탭"
        foreign = "app.이_회차는_지금_연_작품의_원고가_아니에"
        for loc in (self.ko, self.en, self.es):
            self.assertIn(missing, loc)
            self.assertIn(foreign, loc)
        self.assertIn("탭을 닫아주세요", self.ko[missing])
        self.assertNotEqual(self.ko[missing], "소설을 찾을 수 없습니다.")

    def test_local_draft_restore_checks_project_id(self) -> None:
        restore = _slice_function(self.js, "maybeRestoreLocalDraft")
        self.assertIn("draftConflictsWithProject(draft, serverPid)", restore)
        self.assertIn("clearLocalSceneDraft(sceneId)", restore)
        self.assertIn("isCurrentSceneOpenGen(gen)", restore)
        conflict = _slice_function(self.js, "draftConflictsWithProject")
        self.assertIn("draftPid !== serverPid", conflict)

    def test_stale_episode_tabs_pruned_on_project_list_load(self) -> None:
        self.assertIn("function pruneStaleEpisodeTabStorage", self.js)
        load = _slice_function(self.js, "loadProjects")
        self.assertIn("pruneStaleEpisodeTabStorage(state.projects)", load)
        prune = _slice_function(self.js, "pruneStaleEpisodeTabStorage")
        self.assertIn("EPISODE_TABS_PREFIX", prune)
        self.assertIn("localStorage.removeItem(key)", prune)

    def test_tab_and_outline_clicks_verify_current_project(self) -> None:
        request = _slice_function(self.js, "requestOpenScene")
        self.assertIn("sceneBelongsInCurrentOutline(id)", request)
        self.assertIn("dropStaleEpisodeTab(id)", request)
        chrome = _slice_function(self.js, "setupEpisodeChrome")
        self.assertIn("requestOpenScene(id)", chrome)
        self.assertIn("requestOpenScene(sceneId)", self.js)


if __name__ == "__main__":
    unittest.main()
