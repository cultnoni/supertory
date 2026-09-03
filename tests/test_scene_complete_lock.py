"""Complete-lock must not follow the previous scene during openScene GET."""

from __future__ import annotations

import unittest
from pathlib import Path

JS_PATH = Path(__file__).resolve().parents[1] / "web" / "app.js"


def _slice_function(source: str, name: str) -> str:
    needle = f"function {name}("
    start = source.find(needle)
    if start < 0:
        raise AssertionError(f"missing function {name}")
    nxt = source.find("\nfunction ", start + len(needle))
    return source[start:nxt if nxt >= 0 else None]


class SceneCompleteLockSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = JS_PATH.read_text(encoding="utf-8")

    def test_open_scene_pending_lock_until_fields_fill(self) -> None:
        self.assertIn("showSceneEditorPane({ pendingLock: true })", self.js)
        self.assertGreater(
            self.js.count("showSceneEditorPane({ pendingLock: true })"),
            1,
        )
        show = _slice_function(self.js, "showSceneEditorPane")
        self.assertIn("options.pendingLock", show)
        self.assertIn("applySceneCompleteLock({ pending: true })", show)

        fill = _slice_function(self.js, "fillSceneEditorFields")
        status_at = fill.find('$("sceneStatus")).value')
        if status_at < 0:
            status_at = fill.find("$(\"sceneStatus\").value")
        lock_at = fill.rfind("applySceneCompleteLock()")
        self.assertGreater(status_at, 0)
        self.assertGreater(lock_at, status_at)

    def test_restore_local_draft_reapplies_lock(self) -> None:
        restore = _slice_function(self.js, "maybeRestoreLocalDraft")
        status_at = restore.find("draft.status")
        lock_at = restore.find("applySceneCompleteLock()")
        self.assertGreater(status_at, 0)
        self.assertGreater(lock_at, status_at)

    def test_pending_lock_blocks_is_scene_manuscript_locked(self) -> None:
        locked = _slice_function(self.js, "isSceneManuscriptLocked")
        self.assertIn("manuscriptLockPending", locked)
        apply = _slice_function(self.js, "applySceneCompleteLock")
        self.assertIn("options.pending === true", apply)
        self.assertIn("manuscriptLockPending = false", apply)
