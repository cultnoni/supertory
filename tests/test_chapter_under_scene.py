"""Chapter nested under a manuscript (parent_scene_id)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class ChapterUnderSceneTests(unittest.TestCase):
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

    def test_create_chapter_under_scene_appears_in_outline(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "원고 하위 폴더", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201)
        pid = project["id"]

        status, chapter = self.request(
            "POST", f"/api/projects/{pid}/chapters", {"title": "폴더"}
        )
        self.assertEqual(status, 201)

        status, scene = self.request(
            "POST",
            f"/api/chapters/{chapter['id']}/scenes",
            {"title": "원고 A"},
        )
        self.assertEqual(status, 201)

        status, nested = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "하위 폴더", "parent_scene_id": scene["id"]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(nested.get("parent_scene_id"), scene["id"])

        status, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(status, 200)
        top_ids = [int(c["id"]) for c in outline["chapters"]]
        self.assertIn(int(chapter["id"]), top_ids)
        self.assertNotIn(int(nested["id"]), top_ids)

        host = next(c for c in outline["chapters"] if int(c["id"]) == int(chapter["id"]))
        scenes = host.get("scenes") or []
        self.assertEqual(len(scenes), 1)
        child_folders = scenes[0].get("child_chapters") or []
        self.assertEqual(len(child_folders), 1)
        self.assertEqual(int(child_folders[0]["id"]), int(nested["id"]))
        self.assertEqual(child_folders[0]["title"], "하위 폴더")


if __name__ == "__main__":
    unittest.main()
