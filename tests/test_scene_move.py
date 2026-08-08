"""Scene free-move: reorder, nest, and cross-folder placement."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class SceneMoveTests(unittest.TestCase):
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

    def _seed(self) -> dict:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "이동 테스트", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201)
        pid = project["id"]
        status, ch_a = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "폴더A"})
        self.assertEqual(status, 201)
        status, ch_b = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "폴더B"})
        self.assertEqual(status, 201)
        scenes = []
        for title in ("1화", "2화", "3화"):
            status, scene = self.request(
                "POST", f"/api/chapters/{ch_a['id']}/scenes", {"title": title}
            )
            self.assertEqual(status, 201)
            scenes.append(scene)
        return {
            "project_id": pid,
            "chapter_a": ch_a["id"],
            "chapter_b": ch_b["id"],
            "scenes": scenes,
        }

    def _root_titles(self, project_id: int, chapter_id: int) -> list[str]:
        status, outline = self.request("GET", f"/api/projects/{project_id}/outline")
        self.assertEqual(status, 200)
        for chapter in outline["chapters"]:
            if chapter["id"] == chapter_id:
                return [s["title"] for s in chapter.get("scenes") or []]
        return []

    def test_reorder_before_and_after(self) -> None:
        seed = self._seed()
        s1, s2, s3 = seed["scenes"]
        status, result = self.request(
            "POST",
            f"/api/scenes/{s3['id']}/move",
            {"before_scene_id": s1["id"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["moved"])
        self.assertEqual(
            self._root_titles(seed["project_id"], seed["chapter_a"]),
            ["3화", "1화", "2화"],
        )

        status, result = self.request(
            "POST",
            f"/api/scenes/{s1['id']}/move",
            {"after_scene_id": s2["id"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["moved"])
        self.assertEqual(
            self._root_titles(seed["project_id"], seed["chapter_a"]),
            ["3화", "2화", "1화"],
        )

    def test_nest_and_cross_chapter(self) -> None:
        seed = self._seed()
        s1, s2, s3 = seed["scenes"]
        status, result = self.request(
            "POST",
            f"/api/scenes/{s2['id']}/move",
            {
                "chapter_id": seed["chapter_a"],
                "parent_scene_id": s1["id"],
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["moved"])
        self.assertEqual(result["parent_scene_id"], s1["id"])

        status, outline = self.request("GET", f"/api/projects/{seed['project_id']}/outline")
        self.assertEqual(status, 200)
        chapter_a = next(c for c in outline["chapters"] if c["id"] == seed["chapter_a"])
        root = chapter_a["scenes"]
        self.assertEqual([s["title"] for s in root], ["1화", "3화"])
        self.assertEqual([s["title"] for s in root[0].get("children") or []], ["2화"])

        status, result = self.request(
            "POST",
            f"/api/scenes/{s1['id']}/move",
            {
                "chapter_id": seed["chapter_b"],
                "parent_scene_id": None,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["moved"])
        self.assertEqual(result["chapter_id"], seed["chapter_b"])
        # Nested child moves with parent to the new folder.
        status, outline = self.request("GET", f"/api/projects/{seed['project_id']}/outline")
        chapter_b = next(c for c in outline["chapters"] if c["id"] == seed["chapter_b"])
        self.assertEqual([s["title"] for s in chapter_b["scenes"]], ["1화"])
        self.assertEqual(
            [s["title"] for s in chapter_b["scenes"][0].get("children") or []],
            ["2화"],
        )
        self.assertEqual(
            self._root_titles(seed["project_id"], seed["chapter_a"]),
            ["3화"],
        )

    def test_reparent_endpoint_still_works(self) -> None:
        seed = self._seed()
        s1, s2, _s3 = seed["scenes"]
        status, result = self.request(
            "POST",
            f"/api/scenes/{s2['id']}/reparent",
            {"parent_scene_id": s1["id"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["moved"])
        self.assertEqual(result["parent_scene_id"], s1["id"])
