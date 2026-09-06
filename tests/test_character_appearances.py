"""Character appearance index: name/alias mentions in manuscript order."""

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
import scene_character_mentions


class CharacterAppearanceLogicTests(unittest.TestCase):
    def test_alias_only_text_counts_as_mention(self) -> None:
        characters = [
            {"id": 1, "name": "서윤", "aliases": ["여우"]},
            {"id": 2, "name": "해인", "aliases": []},
        ]
        hits = scene_character_mentions.mention_hits(
            "여우가 문을 열고 들어왔다.",
            characters,
        )
        self.assertIn(1, hits)
        self.assertNotIn(2, hits)
        self.assertEqual(hits[1], "여우")

    def test_card_shows_how_they_appeared_and_last_line(self) -> None:
        seoyun = {"id": 1, "name": "서윤", "aliases": ["여우"]}
        haein = {"id": 2, "name": "해인", "aliases": []}
        roster = [seoyun, haein]
        appeared = scene_character_mentions.describe_mention(
            '서윤이 문을 열고 들어왔다.\n"늦었네." 서윤이 말했다.\n"문을 닫아."',
            seoyun,
            roster,
        )
        self.assertEqual(appeared["kind"], "appears")
        self.assertIn("문을 열고 들어왔다", appeared["snippet"])
        self.assertEqual(appeared["last_line"], "문을 닫아.")

        mentioned = scene_character_mentions.describe_mention(
            "서윤 생각이 났다.",
            seoyun,
            roster,
        )
        self.assertEqual(mentioned["kind"], "mentioned")
        self.assertIn("생각이 났다", mentioned["snippet"])
        self.assertEqual(mentioned["last_line"], "")

        other_spoke = scene_character_mentions.describe_mention(
            '"누구야?" 해인이 물었다.',
            seoyun,
            roster,
        )
        self.assertEqual(other_spoke["last_line"], "")
        alias_line = scene_character_mentions.describe_mention(
            '여우가 말했다. "기다려."',
            seoyun,
            roster,
        )
        self.assertEqual(alias_line["last_line"], "기다려.")


class CharacterAppearanceApiTests(unittest.TestCase):
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

    def _save_scene(self, scene_id: int, content: str) -> None:
        status, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200, detail)
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene_id}",
            {
                "title": detail.get("title") or "장면",
                "content_md": content,
                "row_version": detail.get("row_version") or 0,
            },
        )
        self.assertEqual(status, 200, saved)

    def _folder_id_for_chapter(self, project_id: int, chapter_id: int) -> int:
        with app.database() as connection:
            connection.row_factory = sqlite3.Row
            folder_id = folder_tree.folder_id_for_source(
                connection, int(project_id), "chapter", int(chapter_id)
            )
        self.assertIsNotNone(folder_id)
        return int(folder_id)

    def test_save_indexes_alias_mentions_latest_first(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "등장 이력", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, project)
        project_id = int(project["id"])
        status, chapter_a = self.request(
            "POST", f"/api/projects/{project_id}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201, chapter_a)
        status, chapter_b = self.request(
            "POST", f"/api/projects/{project_id}/chapters", {"title": "2장"}
        )
        self.assertEqual(status, 201, chapter_b)
        status, scene_a = self.request(
            "POST", f"/api/chapters/{chapter_a['id']}/scenes", {"title": "첫 등장"}
        )
        self.assertIn(status, (200, 201), scene_a)
        status, scene_b = self.request(
            "POST", f"/api/chapters/{chapter_b['id']}/scenes", {"title": "별칭 등장"}
        )
        self.assertIn(status, (200, 201), scene_b)
        status, character = self.request(
            "POST", f"/api/projects/{project_id}/characters", {"name": "서윤"}
        )
        self.assertEqual(status, 201, character)
        status, _alias = self.request(
            "POST", f"/api/characters/{character['id']}/aliases", {"alias": "여우"}
        )
        self.assertEqual(status, 201, _alias)

        self._save_scene(int(scene_a["id"]), "서윤이 문을 열고 들어왔다.")
        self._save_scene(
            int(scene_b["id"]),
            '여우가 창밖을 보았다.\n"바람이 차다." 여우가 말했다.',
        )

        with app.database() as connection:
            indexed = {
                int(row["scene_id"])
                for row in connection.execute(
                    "SELECT scene_id FROM scene_character_mention WHERE character_id = ?",
                    (int(character["id"]),),
                ).fetchall()
            }
        self.assertEqual(indexed, {int(scene_a["id"]), int(scene_b["id"])})

        status, data = self.request(
            "GET",
            f"/api/projects/{project_id}/character-appearances?character_id={character['id']}",
        )
        self.assertEqual(status, 200, data)
        scene_ids = [int(row["scene_id"]) for row in data["scenes"]]
        self.assertEqual(scene_ids, [int(scene_b["id"]), int(scene_a["id"])])
        self.assertTrue(data["scenes"][0]["latest"])
        self.assertEqual(data["scenes"][0]["matched_label"], "여우")
        self.assertEqual(data["scenes"][0]["kind"], "appears")
        self.assertIn("창밖을 보았다", data["scenes"][0]["snippet"])
        self.assertEqual(data["scenes"][0]["last_line"], "바람이 차다.")

        folder_a = self._folder_id_for_chapter(project_id, int(chapter_a["id"]))
        folder_b = self._folder_id_for_chapter(project_id, int(chapter_b["id"]))
        status, moved = self.request(
            "POST",
            f"/api/folders/{folder_b}/reparent",
            {"new_parent_id": None, "position": "before", "target_id": folder_a},
        )
        self.assertEqual(status, 200, moved)

        status, after = self.request(
            "GET",
            f"/api/projects/{project_id}/character-appearances?character_id={character['id']}",
        )
        self.assertEqual(status, 200, after)
        self.assertEqual(
            [int(row["scene_id"]) for row in after["scenes"]],
            [int(scene_a["id"]), int(scene_b["id"])],
        )

    def test_name_change_reindexes_stale_mentions(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "이름 변경", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, project)
        project_id = int(project["id"])
        status, chapter = self.request(
            "POST", f"/api/projects/{project_id}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201, chapter)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "장면"}
        )
        self.assertIn(status, (200, 201), scene)
        status, character = self.request(
            "POST", f"/api/projects/{project_id}/characters", {"name": "서윤"}
        )
        self.assertEqual(status, 201, character)
        self._save_scene(int(scene["id"]), "서윤이 말했다.")

        status, detail = self.request("GET", f"/api/characters/{character['id']}")
        self.assertEqual(status, 200, detail)
        row_version = (detail.get("character") or {}).get("row_version") or 0
        status, saved = self.request(
            "PUT",
            f"/api/characters/{character['id']}",
            {
                "name": "김서윤",
                "aliases": [],
                "row_version": row_version,
            },
        )
        self.assertEqual(status, 200, saved)

        status, data = self.request(
            "GET",
            f"/api/projects/{project_id}/character-appearances?character_id={character['id']}",
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["scenes"], [])
