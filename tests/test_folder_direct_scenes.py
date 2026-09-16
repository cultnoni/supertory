"""POST /api/folders/{id}/scenes — attach 회차 to any binder folder via transparent host."""

from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import app
import folder_tree
import import_hierarchy


class FolderDirectScenesTests(unittest.TestCase):
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
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _folder_id(self, project_id: int, kind: str, source_id: int) -> int:
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fid = folder_tree.folder_id_for_source(conn, int(project_id), kind, int(source_id))
            self.assertIsNotNone(fid)
            return int(fid)

    def _seed_part_and_chapter(self) -> dict:
        status, project = self.request(
            "POST", "/api/projects", {"title": "폴더 회차", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        pid = project["id"]
        status, part = self.request("POST", f"/api/projects/{pid}/parts", {"title": "1권"})
        self.assertEqual(status, 201)
        status, chapter = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "1부", "part_id": part["id"]},
        )
        self.assertEqual(status, 201)
        return {
            "project_id": pid,
            "part_id": part["id"],
            "chapter_id": chapter["id"],
            "part_folder_id": self._folder_id(pid, "part", part["id"]),
            "chapter_folder_id": self._folder_id(pid, "chapter", chapter["id"]),
        }

    def test_chapters_scenes_route_unchanged(self) -> None:
        seed = self._seed_part_and_chapter()
        status, scene = self.request(
            "POST",
            f"/api/chapters/{seed['chapter_id']}/scenes",
            {"title": "기존경로"},
        )
        self.assertEqual(status, 201, scene)
        self.assertEqual(scene["chapter_id"], seed["chapter_id"])

    def test_folder_scenes_on_chapter_uses_that_chapter(self) -> None:
        seed = self._seed_part_and_chapter()
        status, scene = self.request(
            "POST",
            f"/api/folders/{seed['chapter_folder_id']}/scenes",
            {"title": "부 직속"},
        )
        self.assertEqual(status, 201, scene)
        self.assertEqual(scene["chapter_id"], seed["chapter_id"])
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            parent = conn.execute(
                "SELECT source_kind, source_id FROM folder WHERE id = ?",
                (seed["chapter_folder_id"],),
            ).fetchone()
            self.assertEqual(parent["source_kind"], "chapter")
            self.assertEqual(int(parent["source_id"]), seed["chapter_id"])
            trays = conn.execute(
                "SELECT id FROM folder WHERE parent_id = ? AND notes_md LIKE ?",
                (seed["chapter_folder_id"], f"%{import_hierarchy.TRANSPARENT_CHAPTER_MARKER}%"),
            ).fetchall()
            self.assertEqual(len(trays), 0)

    def test_folder_scenes_on_part_creates_transparent_child(self) -> None:
        seed = self._seed_part_and_chapter()
        status, scene = self.request(
            "POST",
            f"/api/folders/{seed['part_folder_id']}/scenes",
            {"title": "권 직속"},
        )
        self.assertEqual(status, 201, scene)
        self.assertNotEqual(scene["chapter_id"], seed["chapter_id"])
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            parent = conn.execute(
                "SELECT source_kind, source_id FROM folder WHERE id = ?",
                (seed["part_folder_id"],),
            ).fetchone()
            self.assertEqual(parent["source_kind"], "part")
            self.assertEqual(int(parent["source_id"]), seed["part_id"])
            chapter = conn.execute(
                "SELECT part_id, title, notes_md FROM chapter WHERE id = ?",
                (scene["chapter_id"],),
            ).fetchone()
            self.assertEqual(int(chapter["part_id"]), seed["part_id"])
            self.assertIn(import_hierarchy.TRANSPARENT_CHAPTER_MARKER, str(chapter["notes_md"] or ""))
            child = conn.execute(
                "SELECT parent_id, source_kind, source_id, notes_md FROM folder "
                "WHERE source_kind = 'chapter' AND source_id = ?",
                (scene["chapter_id"],),
            ).fetchone()
            self.assertEqual(int(child["parent_id"]), seed["part_folder_id"])
            self.assertIn(import_hierarchy.TRANSPARENT_CHAPTER_MARKER, str(child["notes_md"] or ""))

        status, scene2 = self.request(
            "POST",
            f"/api/folders/{seed['part_folder_id']}/scenes",
            {"title": "권 두번째"},
        )
        self.assertEqual(status, 201, scene2)
        self.assertEqual(scene2["chapter_id"], scene["chapter_id"])

    def test_missing_folder_returns_error(self) -> None:
        status, result = self.request("POST", "/api/folders/999999/scenes", {"title": "없음"})
        self.assertGreaterEqual(status, 400)
        self.assertTrue(result.get("error"))


if __name__ == "__main__":
    unittest.main()
