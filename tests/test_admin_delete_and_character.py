"""Project delete (admin), character trash, and character portrait APIs."""

from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


# 1x1 PNG
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class AdminDeleteAndCharacterTests(unittest.TestCase):
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
        raw = response.read()
        connection.close()
        if not raw:
            return response.status, {}
        try:
            return response.status, json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return response.status, raw

    def test_migration_024_and_project_soft_delete(self) -> None:
        with app.database() as connection:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM schema_migration WHERE version = 24"
                ).fetchone()
            )
            cols = {r[1] for r in connection.execute("PRAGMA table_info(character)").fetchall()}
            self.assertIn("portrait_file", cols)

        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "지울 작품", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201, project)
        pid = project["id"]

        status, listed = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertTrue(any(p["id"] == pid for p in listed))

        status, result = self.request("DELETE", f"/api/projects/{pid}")
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("ok"))

        status, listed2 = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertFalse(any(p["id"] == pid for p in listed2))

    def test_character_delete_and_portrait(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "인물 테스트", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201, project)
        pid = project["id"]

        status, character = self.request(
            "POST",
            f"/api/projects/{pid}/characters",
            {"name": "지우"},
        )
        self.assertEqual(status, 201, character)
        cid = character["id"]

        status, detail = self.request("GET", f"/api/characters/{cid}")
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["character"]["name"], "지우")

        status, portrait = self.request(
            "POST",
            f"/api/characters/{cid}/portrait",
            {
                "filename": "face.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1).decode("ascii"),
            },
        )
        self.assertEqual(status, 200, portrait)
        self.assertTrue(portrait.get("portrait_url"))

        # Image bytes download
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", f"/api/characters/{cid}/portrait")
        response = connection.getresponse()
        data = response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertTrue(data.startswith(b"\x89PNG"))

        status, cleared = self.request("DELETE", f"/api/characters/{cid}/portrait")
        self.assertEqual(status, 200, cleared)

        status, deleted = self.request("DELETE", f"/api/characters/{cid}")
        self.assertEqual(status, 200, deleted)
        self.assertTrue(deleted.get("ok"))

        status, gone = self.request("GET", f"/api/characters/{cid}")
        self.assertIn(status, (400, 404), gone)

        status, remaining = self.request("GET", f"/api/projects/{pid}/characters")
        self.assertEqual(status, 200)
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
