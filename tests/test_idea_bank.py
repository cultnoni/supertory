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
        status, project = self.request("POST", "/api/projects", {"title": "아이디어 연습", "purpose": "novel"})
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

        status, result = self.request("DELETE", f"/api/ideas/{idea['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(result["ok"], True)
        status, empty = self.request("GET", f"/api/projects/{project['id']}/ideas")
        self.assertEqual(status, 200)
        self.assertEqual(empty, [])


if __name__ == "__main__":
    unittest.main()
