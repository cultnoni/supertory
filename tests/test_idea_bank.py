"""Tests for the idea bank sticky notes."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class IdeaBankApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_create_update_delete_idea_notes(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "아이디어 연습", "purpose": "novel", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201)

        status, idea = self.request("POST", f"/api/projects/{project['id']}/ideas", {
            "title": "결말 복선",
            "body_md": "편지 안에 지도가 있다",
            "color": "pink",
        })
        self.assertEqual(status, 201)
        self.assertEqual(idea["title"], "결말 복선")
        self.assertEqual(idea["color"], "pink")

        status, listed = self.request("GET", f"/api/projects/{project['id']}/ideas")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed), 1)

        status, updated = self.request("PUT", f"/api/ideas/{idea['id']}", {
            "title": "결말 복선 수정",
            "body_md": "지도 대신 열쇠",
            "color": "blue",
        })
        self.assertEqual(status, 200)
        self.assertEqual(updated["body_md"], "지도 대신 열쇠")
        self.assertEqual(updated["color"], "blue")

        status, pinned = self.request("PUT", f"/api/ideas/{idea['id']}", {
            "is_pinned": 1,
        })
        self.assertEqual(status, 200)
        self.assertEqual(int(pinned["is_pinned"]), 1)

        status, listed = self.request("GET", f"/api/projects/{project['id']}/ideas")
        self.assertEqual(status, 200)
        self.assertEqual(int(listed[0]["is_pinned"]), 1)

        status, unpinned = self.request("PUT", f"/api/ideas/{idea['id']}", {
            "is_pinned": 0,
        })
        self.assertEqual(status, 200)
        self.assertEqual(int(unpinned["is_pinned"]), 0)

        status, result = self.request("DELETE", f"/api/ideas/{idea['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(result["ok"], True)
        status, empty = self.request("GET", f"/api/projects/{project['id']}/ideas")
        self.assertEqual(status, 200)
        self.assertEqual(empty, [])

    def test_idea_pin_migration_applied(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 33"
            ).fetchone()
            self.assertIsNotNone(row)
            cols = {
                item["name"]
                for item in connection.execute("PRAGMA table_info(idea_note)").fetchall()
            }
            self.assertIn("is_pinned", cols)


class IdeaFloatPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text(
            encoding="utf-8"
        )
        cls.css = (Path(__file__).resolve().parent.parent / "web" / "styles.css").read_text(
            encoding="utf-8"
        )

    def test_same_project_load_keeps_idea_floats(self) -> None:
        load = self.js.split("async function loadProject()", 1)[1].split(
            "function previewLines", 1
        )[0]
        self.assertIn("ideaFloatProjectChanged", load)
        self.assertIn("if (ideaFloatProjectChanged) closeAllIdeaFloats();", load)
        self.assertNotIn(
            'if (typeof closeAllIdeaFloats === "function") closeAllIdeaFloats();',
            load,
        )
        self.assertIn("pruneAndSyncIdeaFloats();", load)

    def test_float_host_is_body_overlay(self) -> None:
        self.assertIn("if (host.parentElement !== document.body)", self.js)
        self.assertIn("document.body.appendChild(host)", self.js)
        self.assertIn("const ideaFloatLayouts = new Map()", self.js)
        self.assertIn("z-index: 220;", self.css)
        self.assertRegex(
            self.css,
            r"\.idea-float-host\s*\{[^}]*pointer-events:\s*none",
        )


class SceneAuthorNotesIdeaBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parent.parent
        cls.js = (root / "web" / "app.js").read_text(encoding="utf-8")
        cls.html = (root / "web" / "index.html").read_text(encoding="utf-8")
        cls.locales = {
            lang: (root / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8")
            for lang in ("ko", "en", "es")
        }

    def test_idea_bank_has_episode_author_notes_tab(self) -> None:
        self.assertIn('id="sceneAuthorNotesList"', self.html)
        self.assertIn('id="ideaBoardSceneNotes"', self.html)
        self.assertIn('id="ideaBoardTabSceneNotes"', self.html)
        self.assertIn("[data-idea-bank-tab]", self.js.split("function setupIdeaBank()", 1)[1].split("async function refreshAiStatus", 1)[0])
        self.assertIn('document.querySelectorAll("[data-idea-bank-tab]")', self.js.split("function applyIdeaBankPane()", 1)[1].split("function setIdeaBankPane", 1)[0])
        self.assertIn('data-idea-bank-tab="sceneNotes"', self.html)
        self.assertIn('id="ideaBankTabSceneNotes"', self.html)
        self.assertIn("function getSceneAuthorNotesSequence()", self.js)
        self.assertIn("getEpisodeSequence()", self.js.split("function getSceneAuthorNotesSequence()", 1)[1])
        self.assertIn("if (!notes.trim() && !sceneAuthorNotesOpenIds.has(sceneId)) continue;", self.js)
        self.assertIn('openSceneToolsDrawer("notes", { force: true })', self.js)
        self.assertIn("patchOutlineSceneNotes", self.js)
        self.assertIn("markSceneDirty()", self.js.split("function onSceneAuthorNoteEditorInput", 1)[1].split("function scheduleOtherSceneAuthorNotesSave", 1)[0])
        self.assertIn('notes_md: text', self.js.split("async function persistOtherSceneAuthorNotes", 1)[1].split("async function flushPendingSceneAuthorNotesSaves", 1)[0])
        for text in self.locales.values():
            self.assertIn('"app.회차별_작가메모"', text)
            self.assertIn('"app.회차별_작가메모_안내"', text)
            self.assertIn('"app.이_화로_이동"', text)
            self.assertIn('"app.작성된_작가메모가_없어요"', text)


if __name__ == "__main__":
    unittest.main()
