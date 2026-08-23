"""Binder: drop a nested manuscript onto a parent folder, not onto sibling folders."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OutlineSceneDropUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_folder_drop_uses_direct_scenes_only(self) -> None:
        fn = self.js.split("function resolveFolderSectionSceneDrop", 1)[1].split(
            "function resolveSceneMoveDrop", 1
        )[0]
        self.assertIn("directSceneLinksInHost", fn)
        self.assertIn("sceneHostSectionForFolder", fn)
        self.assertIn(":scope > .scene-tree-item", self.js)
        self.assertNotIn(
            ':scope .scene-tree-item[data-depth="0"]',
            fn,
        )

    def test_empty_transparent_trays_are_deduped(self) -> None:
        self.assertIn("function withoutEmptyDuplicateTransparentFolders", self.js)
        self.assertIn("supertory:transparent_volume", self.js)
