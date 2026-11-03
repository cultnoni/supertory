"""Closing the first-run tour must always undo its forced scene workspace."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _slice_function(source: str, name: str) -> str:
    start = source.find(f"function {name}(")
    if start < 0:
        raise AssertionError(f"missing function {name}")
    end = source.find("\nfunction ", start + 1)
    return source[start:end if end >= 0 else None]


class OnboardingTutorialCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_close_does_not_depend_on_on_destroyed(self) -> None:
        # driver.js skips onDestroyed when the tour is closed mid-animation,
        # which left .tutorial-force-visible on #sceneWorkspace over every board.
        start = _slice_function(self.js, "startOnboardingTutorial")
        self.assertIn("onDestroyStarted:", start)
        hook = start.split("onDestroyStarted:", 1)[1].split("},", 1)[0]
        self.assertIn("markTutorialCompleted()", hook)
        self.assertIn("destroyOnboardingDriver()", hook)

    def test_destroy_restores_workspace(self) -> None:
        destroy = _slice_function(self.js, "destroyOnboardingDriver")
        self.assertIn("restoreTutorialUi()", destroy)
        restore = _slice_function(self.js, "restoreTutorialUi")
        self.assertIn('classList.remove("tutorial-force-visible")', restore)


if __name__ == "__main__":
    unittest.main()
