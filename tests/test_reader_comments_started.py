"""Per-scene virtual-reader comments started flag (migration 047)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class ReaderCommentsStartedTests(unittest.TestCase):
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

    def test_flag_defaults_off_and_marks_started(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "댓글 플래그"})
        self.assertEqual(status, 201)
        status, chapter = self.request(
            "POST", f"/api/projects/{project['id']}/chapters", {"title": "장"}
        )
        self.assertEqual(status, 201)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "회차"}
        )
        self.assertEqual(status, 201)
        scene_id = scene["id"]

        status, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200)
        self.assertEqual(int(detail.get("reader_comments_started") or 0), 0)

        status, marked = self.request("POST", f"/api/scenes/{scene_id}/reader-comments-started")
        self.assertEqual(status, 200, marked)
        self.assertEqual(int(marked.get("reader_comments_started") or 0), 1)

        status, again = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200)
        self.assertEqual(int(again.get("reader_comments_started") or 0), 1)
        self.assertEqual(again.get("row_version"), detail.get("row_version"))
