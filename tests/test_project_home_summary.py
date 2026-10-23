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

    def test_outline_scenes_include_char_counts(self) -> None:
        _, project = self.request("POST", "/api/projects", {"title": "글자 수 작품", "main_genre": "판타지"})
        pid = project["id"]
        _, chapter = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "1부"})
        _, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"})
        _, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        status, _ = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": "1화",
                "status": "draft",
                "synopsis_md": "",
                "notes_md": "",
                "content_md": "<p>안녕 하세요</p><p>작가님 반가워요</p>",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200)
        status, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(status, 200)
        found = []

        def walk(node) -> None:
            if isinstance(node, dict):
                if node.get("id") == scene["id"] and "char_counts" in node:
                    found.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(outline)
        self.assertTrue(found, "회차에 char_counts가 있어야 해요")
        counts = found[0]["char_counts"]
        # 본문 표시줄과 같은 방식: 문단 사이에 줄바꿈 없이 이어 붙여 센다.
        glued = "안녕 하세요작가님 반가워요"
        self.assertEqual(counts["chars_with_space"], len(glued))
        self.assertEqual(counts["chars_no_space"], len(glued.replace(" ", "")))
        self.assertEqual(counts["words"], 3)
        self.assertEqual(counts["letters"], len(glued.replace(" ", "")))

    def test_char_count_cache_is_not_shared_between_databases(self) -> None:
        """다른 DB에서 저장본 번호가 같아도 이전 DB의 캐시 값이 섞이면 안 된다(CI 전체 실행에서 발견된 버그)."""

        def outline_counts(content: str) -> dict:
            _, project = self.request("POST", "/api/projects", {"title": "캐시 확인", "main_genre": "판타지"})
            pid = project["id"]
            _, chapter = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "1부"})
            _, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"})
            _, detail = self.request("GET", f"/api/scenes/{scene['id']}")
            status, _ = self.request("PUT", f"/api/scenes/{scene['id']}", {
                "title": "1화", "status": "draft", "synopsis_md": "", "notes_md": "",
                "content_md": content, "row_version": detail["row_version"],
            })
            self.assertEqual(status, 200)
            _, outline = self.request("GET", f"/api/projects/{pid}/outline")
            found = []

            def walk(node) -> None:
                if isinstance(node, dict):
                    if node.get("id") == scene["id"] and "char_counts" in node:
                        found.append(node["char_counts"])
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)

            walk(outline)
            self.assertTrue(found)
            return found[0]

        first = outline_counts("<p>가나다</p>")
        self.assertEqual(first["chars_with_space"], 3)

        # 같은 모양(같은 id)의 다른 DB를 열고, 본문 길이는 같지만 내용이 다른 글을 저장한다.
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        app.DATA_DIR = Path(other.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        second = outline_counts("<p>가나다라마바사</p>")
        self.assertEqual(second["chars_with_space"], 7)


if __name__ == "__main__":
    unittest.main()
