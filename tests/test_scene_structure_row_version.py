"""Structure-only scene UPDATEs must not bump row_version (in-flight editor save)."""

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


class SceneStructureRowVersionTests(unittest.TestCase):
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

    def _seed_three_scenes(self) -> dict:
        status, project = self.request(
            "POST", "/api/projects", {"title": "구조 버전", "main_genre": "판타지"}
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

    def _held_version(self, scene_id: int) -> int:
        status, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200)
        return int(detail["row_version"] or 0)

    def _assert_put_with_held(self, scene_id: int, title: str, held_version: int) -> None:
        status, again = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200)
        self.assertEqual(int(again["row_version"] or 0), held_version)
        status, saved = self.request("PUT", f"/api/scenes/{scene_id}", {
            "title": title,
            "status": "draft",
            "content_md": "편집 중이던 본문",
            "row_version": held_version,
        })
        self.assertEqual(status, 200, saved)
        self.assertGreater(int(saved["row_version"]), held_version)
        self.assertEqual(saved.get("ok"), True)

    def test_reorder_sibling_keeps_row_version(self) -> None:
        seed = self._seed_three_scenes()
        s1, _s2, s3 = seed["scenes"]
        held = self._held_version(s1["id"])

        status, result = self.request(
            "POST",
            f"/api/scenes/{s3['id']}/move",
            {"before_scene_id": s1["id"]},
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(
            self._root_titles(seed["project_id"], seed["chapter_a"]),
            ["3화", "1화", "2화"],
        )
        self._assert_put_with_held(s1["id"], "1화", held)

    def test_duplicate_sibling_keeps_row_version(self) -> None:
        seed = self._seed_three_scenes()
        s1, s2, _s3 = seed["scenes"]
        held = self._held_version(s2["id"])

        status, dup = self.request("POST", f"/api/scenes/{s1['id']}/duplicate", {})
        self.assertEqual(status, 201, dup)
        titles = self._root_titles(seed["project_id"], seed["chapter_a"])
        self.assertEqual(titles[0], "1화")
        self.assertIn("복제", titles[1])
        self.assertEqual(titles[2:], ["2화", "3화"])
        self._assert_put_with_held(s2["id"], "2화", held)

    def test_move_away_keeps_leftover_sibling_row_version(self) -> None:
        seed = self._seed_three_scenes()
        s1, _s2, s3 = seed["scenes"]
        held = self._held_version(s3["id"])

        status, result = self.request(
            "POST",
            f"/api/scenes/{s1['id']}/move",
            {"chapter_id": seed["chapter_b"], "parent_scene_id": None},
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result["chapter_id"], seed["chapter_b"])
        self.assertEqual(
            self._root_titles(seed["project_id"], seed["chapter_a"]),
            ["2화", "3화"],
        )
        self.assertEqual(
            self._root_titles(seed["project_id"], seed["chapter_b"]),
            ["1화"],
        )
        self._assert_put_with_held(s3["id"], "3화", held)

    def test_folder_sync_keeps_row_version_and_folder_id(self) -> None:
        seed = self._seed_three_scenes()
        scene_id = seed["scenes"][0]["id"]
        held = self._held_version(scene_id)

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            before = conn.execute(
                "SELECT folder_id FROM scene WHERE id = ?", (scene_id,)
            ).fetchone()
            self.assertIsNotNone(before["folder_id"])
            folder_tree.sync_project_folder_tree(conn, seed["project_id"])
            after = conn.execute(
                "SELECT folder_id, row_version FROM scene WHERE id = ?",
                (scene_id,),
            ).fetchone()
            self.assertEqual(int(after["folder_id"]), int(before["folder_id"]))
            self.assertEqual(int(after["row_version"] or 0), held)

        self._assert_put_with_held(scene_id, "1화", held)

    def test_flatten_keeps_row_version_and_order(self) -> None:
        seed = self._seed_three_scenes()
        s1, s2, s3 = seed["scenes"]
        status, nested = self.request(
            "POST",
            f"/api/scenes/{s2['id']}/move",
            {"chapter_id": seed["chapter_a"], "parent_scene_id": s1["id"]},
        )
        self.assertEqual(status, 200, nested)

        held_s1 = self._held_version(s1["id"])
        held_s3 = self._held_version(s3["id"])

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            folder_tree._flatten_nested_scenes(conn, seed["chapter_a"])
            rows = conn.execute(
                "SELECT id, parent_scene_id, sort_order, row_version "
                "FROM scene WHERE chapter_id = ? AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (seed["chapter_a"],),
            ).fetchall()
        titles_by_id = {s["id"]: s["title"] for s in (s1, s2, s3)}
        self.assertEqual(
            [titles_by_id[int(r["id"])] for r in rows],
            ["1화", "2화", "3화"],
        )
        for row in rows:
            self.assertIsNone(row["parent_scene_id"])
        version_by_id = {int(r["id"]): int(r["row_version"] or 0) for r in rows}
        self.assertEqual(version_by_id[s1["id"]], held_s1)
        self.assertEqual(version_by_id[s3["id"]], held_s3)

        self._assert_put_with_held(s1["id"], "1화", held_s1)
        self._assert_put_with_held(s3["id"], "3화", held_s3)
