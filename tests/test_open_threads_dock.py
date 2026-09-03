"""Open-threads dock: normalize resolved flag, binder-order listing, PATCH."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class OpenThreadsDockTests(unittest.TestCase):
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

    def create_project_with_two_scenes(self) -> tuple[int, int, int]:
        status, project = self.request("POST", "/api/projects", {"title": "떡밥작품", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        pid = int(project["id"])
        status, chapter = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "1장"})
        self.assertEqual(status, 201, chapter)
        status, first = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"})
        self.assertEqual(status, 201, first)
        status, second = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "2화"})
        self.assertEqual(status, 201, second)
        return pid, int(first["id"]), int(second["id"])

    def seed_threads(self, project_id: int, threads: list) -> None:
        with app.database() as connection:
            connection.execute(
                "INSERT INTO project_index(project_id, open_threads_json) VALUES (?, ?) "
                "ON CONFLICT(project_id) DO UPDATE SET open_threads_json = excluded.open_threads_json",
                (project_id, json.dumps(threads, ensure_ascii=False)),
            )

    def seed_summary(self, scene_id: int, new_threads: list[str]) -> None:
        status, result = self.request(
            "PUT",
            f"/api/scenes/{scene_id}/summary",
            {
                "summary": {
                    "event_summary": "사건",
                    "characters_involved": [],
                    "new_world_facts": [],
                    "new_threads": new_threads,
                    "resolved_threads": [],
                    "tracked_facts": [],
                }
            },
        )
        self.assertEqual(status, 200, result)

    def test_normalize_strings_and_objects(self) -> None:
        normalized = app.SuperToryHandler._normalize_open_threads(
            ["칼의 주인", {"text": "사라진 편지", "resolved": True}, "칼의 주인", ""]
        )
        self.assertEqual(
            normalized,
            [
                {"text": "칼의 주인", "resolved": False},
                {"text": "사라진 편지", "resolved": True},
            ],
        )

    def test_migration_rewrites_string_list(self) -> None:
        pid, _, _ = self.create_project_with_two_scenes()
        self.seed_threads(pid, ["옛 문자열 떡밥"])
        with app.database() as connection:
            connection.execute("DELETE FROM schema_migration WHERE version = 82")
            app.apply_migration_082(connection)
            row = connection.execute(
                "SELECT open_threads_json FROM project_index WHERE project_id = ?",
                (pid,),
            ).fetchone()
            version = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 82"
            ).fetchone()
        stored = json.loads(row["open_threads_json"])
        self.assertEqual(stored, [{"text": "옛 문자열 떡밥", "resolved": False}])
        self.assertEqual(version[0], "open_threads_resolved")

    def test_list_sorts_unresolved_first_and_joins_scene(self) -> None:
        pid, scene_a, scene_b = self.create_project_with_two_scenes()
        self.seed_summary(scene_a, ["먼저 나온 떡밥"])
        self.seed_summary(scene_b, ["나중에 나온 떡밥"])
        self.seed_threads(
            pid,
            [
                {"text": "나중에 나온 떡밥", "resolved": False},
                {"text": "먼저 나온 떡밥", "resolved": True},
                "회차 없는 떡밥",
            ],
        )
        status, data = self.request("GET", f"/api/projects/{pid}/open-threads")
        self.assertEqual(status, 200, data)
        texts = [row["text"] for row in data["threads"]]
        self.assertEqual(texts[0], "나중에 나온 떡밥")
        self.assertFalse(data["threads"][0]["resolved"])
        self.assertEqual(data["threads"][0]["scene_id"], scene_b)
        resolved = [row for row in data["threads"] if row["text"] == "먼저 나온 떡밥"][0]
        self.assertTrue(resolved["resolved"])
        self.assertEqual(resolved["scene_id"], scene_a)
        unknown = [row for row in data["threads"] if row["text"] == "회차 없는 떡밥"][0]
        self.assertIsNone(unknown["scene_id"])

    def test_patch_resolved_persists_and_reopen(self) -> None:
        pid, _, _ = self.create_project_with_two_scenes()
        self.seed_threads(pid, ["숨겨진 문"])
        status, data = self.request(
            "PATCH",
            f"/api/projects/{pid}/open-threads",
            {"text": "숨겨진 문", "resolved": True},
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["threads"][0]["resolved"])
        status, again = self.request("GET", f"/api/projects/{pid}/open-threads")
        self.assertEqual(status, 200, again)
        self.assertTrue(again["threads"][0]["resolved"])
        status, undone = self.request(
            "PATCH",
            f"/api/projects/{pid}/open-threads",
            {"text": "숨겨진 문", "resolved": False},
        )
        self.assertEqual(status, 200, undone)
        self.assertFalse(undone["threads"][0]["resolved"])

    def test_merge_keeps_user_resolved(self) -> None:
        merged = app.SuperToryHandler._merge_open_thread_state(
            [{"text": "유지할 떡밥", "resolved": True}, {"text": "열린 떡밥", "resolved": False}],
            ["열린 떡밥", "새로 생긴 떡밥"],
        )
        by_text = {item["text"]: item for item in merged}
        self.assertTrue(by_text["유지할 떡밥"]["resolved"])
        self.assertFalse(by_text["열린 떡밥"]["resolved"])
        self.assertFalse(by_text["새로 생긴 떡밥"]["resolved"])

    def test_index_get_returns_objects(self) -> None:
        pid, _, _ = self.create_project_with_two_scenes()
        self.seed_threads(pid, ["문자열 떡밥"])
        status, index = self.request("GET", f"/api/projects/{pid}/index")
        self.assertEqual(status, 200, index)
        self.assertEqual(index["open_threads"], [{"text": "문자열 떡밥", "resolved": False}])


if __name__ == "__main__":
    unittest.main()
