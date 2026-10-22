"""GET /api/projects/{id}/home-summary — 시작 화면 최근 작품 카드 요약."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class ProjectHomeSummaryTests(unittest.TestCase):
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

    def test_empty_project_counts_are_zero(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "빈 작품", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        status, summary = self.request("GET", f"/api/projects/{project['id']}/home-summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["episode_count"], 0)
        self.assertEqual(summary["char_count"], 0)
        self.assertEqual(summary["status_counts"], {})

    def test_counts_episodes_chars_and_status(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "요약 작품", "main_genre": "판타지"})
        pid = project["id"]
        _, part = self.request("POST", f"/api/projects/{pid}/parts", {"title": "1권"})
        _, chapter = self.request(
            "POST", f"/api/projects/{pid}/chapters", {"title": "1부", "part_id": part["id"]}
        )
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201, scene)
        _, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        status, _ = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": "1화",
                "status": "draft",
                "synopsis_md": "",
                "notes_md": "",
                "content_md": "<p>안녕하세요 작가님</p>",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200)
        status, summary = self.request("GET", f"/api/projects/{pid}/home-summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["episode_count"], 1)
        self.assertGreater(summary["char_count"], 0)
        self.assertGreaterEqual(summary["volume_count"], 1)
        self.assertEqual(summary["status_counts"], {"draft": 1})

    def test_unknown_project_is_404(self) -> None:
        status, _ = self.request("GET", "/api/projects/99999/home-summary")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
