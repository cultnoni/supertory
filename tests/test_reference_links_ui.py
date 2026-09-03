"""Frontend contract: scene reference kind must not treat sourceId as a file."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReferenceKindUiTests(unittest.TestCase):
    def test_normalize_does_not_treat_source_id_as_file(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function referenceItemKind", app_js)
        self.assertIn("function normalizeReferenceItem", app_js)
        self.assertNotIn(
            'raw.kind === "file" || raw.fileName || raw.sourceId',
            app_js,
        )
        helper = app_js.split("function referenceItemKind", 1)[1].split(
            "function sourceEntryIsFile", 1
        )[0]
        self.assertIn('explicit === "link"', helper)
        self.assertIn('explicit === "file"', helper)
        self.assertIn("fileName ? \"file\" : \"link\"", helper)
        self.assertNotIn("sourceId", helper)

        normalize = app_js.split("function normalizeReferenceItem", 1)[1].split(
            "function upsertProjectSourceFromReference", 1
        )[0]
        self.assertIn("referenceItemKind(raw)", normalize)
        self.assertIn("sourceId: String(raw.sourceId", normalize)

    def test_reference_materials_badge_updates_after_add_and_remove(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        count_fn = app_js.split("function countReferenceMaterials", 1)[1].split(
            "function updateReferenceMaterialsBadge", 1
        )[0]
        self.assertIn("list.filter((raw) => raw != null).length", count_fn)
        self.assertNotIn("item.url", count_fn)

        render_fn = app_js.split("function renderReferenceLinks", 1)[1].split(
            "function collectReferenceLinksFromState", 1
        )[0]
        self.assertGreater(
            render_fn.rfind("updateReferenceMaterialsBadge"),
            render_fn.find("list.innerHTML"),
        )

        add_fn = app_js.split("function addReferenceLink", 1)[1].split(
            "async function addReferenceFileFromPicker", 1
        )[0]
        self.assertLess(add_fn.find(".push("), add_fn.find("renderReferenceLinks"))

        self.assertIn("state.referenceLinks.splice(index, 1)", render_fn)
        splice_at = render_fn.find("state.referenceLinks.splice(index, 1)")
        render_after_splice = render_fn.find("renderReferenceLinks()", splice_at)
        self.assertGreater(render_after_splice, splice_at)

        notes_input = app_js.split('$("sceneNotes")?.addEventListener("input"', 1)[1][:400]
        self.assertIn("updateAuthorNotesBadge", notes_input)
        characters_render = app_js.split("function renderSceneCharacters", 1)[1].split(
            "async function refreshSceneCharactersPanel", 1
        )[0]
        self.assertGreater(
            characters_render.rfind("updateSceneCharactersBadge"),
            characters_render.find("list.innerHTML"),
        )
