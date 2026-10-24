"""장르 레이어(수수께끼형·세계형) 실행 테스트가 함께 쓰는 서버·프로젝트 도구 (가짜 Claude, 임시 DB)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app
import feedback_api
from feedback_pipeline.claude_client import set_client_for_tests, set_configured_for_tests
from test_feedback_pipeline import MANUSCRIPT


class GenreRunHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        feedback_api.reset_runtime_for_tests()
        set_configured_for_tests(True)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        deadline = time.time() + 20
        while time.time() < deadline and feedback_api._active_count():
            time.sleep(0.05)
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        set_client_for_tests(None)
        set_configured_for_tests(None)
        feedback_api.reset_runtime_for_tests()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, (json.loads(raw) if raw else {})

    def create_scene(self, main_genre: str, **extra) -> tuple[int, int]:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "추리", "purpose": "genre_literature", "main_genre": main_genre,
             "cluster_id": "genre_literature", **extra},
        )
        self.assertEqual(status, 201, project)
        project_id = int(project["id"])
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1장"})
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"})
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {"title": "1화", "content_md": MANUSCRIPT, "status": "draft", "row_version": detail.get("row_version") or 1},
        )
        self.assertEqual(status, 200, saved)
        return project_id, int(scene["id"])

    def run_and_wait(self, project_id: int, scene_id: int) -> dict:
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/feedback/runs", {"scene_id": scene_id}
        )
        self.assertIn(status, (200, 201), created)
        run_id = int(created["run_id"])
        deadline = time.time() + 30
        run: dict = {}
        while time.time() < deadline:
            status, run = self.request("GET", f"/api/feedback/runs/{run_id}")
            if run.get("status") in {"ok", "partial", "failed"}:
                return run
            time.sleep(0.05)
        self.fail(f"실행이 끝나지 않았습니다: {run}")

