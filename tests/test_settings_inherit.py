"""Copy world / characters / items (and optional chronicle + relations) into a new project."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import character_import_analysis
from world_import_analysis import compose_worldbuilding_md, parse_worldbuilding_md


class SettingsInheritHttpTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=30
        )
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = raw
        return response.status, data

    def _project(self, title: str) -> int:
        status, project = self.request("POST", "/api/projects", {"title": title, "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def _character(self, project_id: int, name: str, **fields) -> int:
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/characters",
            {"name": name},
        )
        self.assertEqual(status, 201, created)
        cid = int(created["id"])
        status, detail = self.request("GET", f"/api/characters/{cid}")
        self.assertEqual(status, 200, detail)
        body = {
            "name": name,
            "profile_md": fields.get("profile_md", ""),
            "short_description": fields.get("short_description", ""),
            "strengths_md": fields.get("strengths_md", ""),
            "weaknesses_md": fields.get("weaknesses_md", ""),
            "author_notes_md": fields.get("author_notes_md", ""),
            "row_version": detail["character"]["row_version"],
        }
        if "aliases" in fields:
            body["aliases"] = fields["aliases"]
        status, _ = self.request("PUT", f"/api/characters/{cid}", body)
        self.assertEqual(status, 200)
        return cid

    def _item(self, project_id: int, name: str, description: str, aliases: list[str] | None = None) -> int:
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/items",
            {"name": name, "description": description},
        )
        self.assertEqual(status, 201, created)
        item_id = int(created["id"])
        if aliases:
            status, detail = self.request("GET", f"/api/items/{item_id}")
            self.assertEqual(status, 200, detail)
            status, _ = self.request(
                "PUT",
                f"/api/items/{item_id}",
                {
                    "name": name,
                    "description": description,
                    "aliases": aliases,
                    "row_version": detail["item"]["row_version"],
                },
            )
            self.assertEqual(status, 200)
        return item_id

    def _seed_source(self) -> dict:
        pid = self._project("전작 달빛")
        a = self._character(
            pid,
            "비비",
            profile_md="빨간 머리",
            short_description="달빛 후계자",
            strengths_md="흑염검",
            weaknesses_md="고독",
            author_notes_md="전작에만 남을 메모",
            aliases=["흑염의 후계"],
        )
        b = self._character(pid, "엔케", profile_md="비비의 주인")
        item_id = self._item(pid, "흑염검", "달빛을 먹는 검은 칼날", aliases=["흑검"])
        world = compose_worldbuilding_md({"locale": "해안 도시 하버라인", "heritage": "검은 비의 기원"})
        status, _ = self.request("PUT", f"/api/projects/{pid}/settings", {"worldbuilding_md": world})
        self.assertEqual(status, 200)
        status, chapter = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "1장"})
        self.assertEqual(status, 201, chapter)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"})
        self.assertEqual(status, 201, scene)
        with app.database() as connection:
            connection.execute(
                "INSERT INTO character_trait_history"
                "(character_id, project_id, scene_id, field_name, detected_content, applied) "
                "VALUES (?, ?, ?, 'profile_md', '검은 머리', 1)",
                (a, pid, int(scene["id"])),
            )
            connection.execute(
                "INSERT INTO item_trait_history"
                "(item_id, project_id, scene_id, field_name, detected_content, applied) "
                "VALUES (?, ?, ?, 'description', '달빛을 먹는다', 0)",
                (item_id, pid, int(scene["id"])),
            )
        status, rel = self.request(
            "POST",
            f"/api/projects/{pid}/character-relations",
            {"character_a_id": a, "character_b_id": b, "label": "연인"},
        )
        self.assertEqual(status, 201, rel)
        return {
            "id": pid,
            "a": a,
            "b": b,
            "item": item_id,
            "scene": int(scene["id"]),
            "world": world,
        }

    def test_inherit_copies_settings_without_notes_chronicle_or_relations(self) -> None:
        source = self._seed_source()
        status, created = self.request(
            "POST",
            "/api/projects",
            {
                "title": "속편",
                "main_genre": "판타지",
                "inherit_from_project_id": source["id"],
                "inherit_chronicle": False,
            },
        )
        self.assertEqual(status, 201, created)
        dest = int(created["id"])
        self.assertEqual(created["inherited_from_title"], "전작 달빛")
        self.assertFalse(created.get("inherited_chronicle"))

        status, listed = self.request("GET", "/api/projects")
        self.assertEqual(status, 200, listed)
        settings = next(row for row in listed if int(row["id"]) == dest)
        values = parse_worldbuilding_md(settings.get("worldbuilding_md"))
        self.assertTrue(character_import_analysis.is_tori_text(values["locale"]))
        self.assertIn("하버라인", values["locale"])
        self.assertTrue(character_import_analysis.is_tori_text(values["heritage"]))

        status, characters = self.request("GET", f"/api/projects/{dest}/characters")
        self.assertEqual(status, 200, characters)
        by_name = {row["name"]: row for row in characters}
        self.assertIn("비비", by_name)
        self.assertIn("엔케", by_name)
        self.assertNotEqual(int(by_name["비비"]["id"]), source["a"])
        self.assertTrue(character_import_analysis.is_tori_text(by_name["비비"]["short_description"]))
        self.assertTrue(character_import_analysis.is_tori_text(by_name["비비"]["profile_md"]))
        self.assertIn("〔토리〕", " ".join(by_name["비비"].get("aliases") or []))

        status, detail = self.request("GET", f"/api/characters/{by_name['비비']['id']}")
        self.assertEqual(status, 200, detail)
        self.assertEqual(str(detail["character"].get("author_notes_md") or ""), "")

        status, items = self.request("GET", f"/api/projects/{dest}/items")
        self.assertEqual(status, 200, items)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "흑염검")
        self.assertTrue(character_import_analysis.is_tori_text(items[0]["description"]))
        status, item_detail = self.request("GET", f"/api/items/{items[0]['id']}")
        self.assertEqual(status, 200, item_detail)
        self.assertIn("〔토리〕", " ".join(row["alias"] for row in item_detail["aliases"]))

        status, history = self.request("GET", f"/api/characters/{by_name['비비']['id']}/trait-history")
        self.assertEqual(status, 200, history)
        self.assertEqual(history.get("entries") or [], [])
        status, canvas = self.request("GET", f"/api/projects/{dest}/character-canvas")
        self.assertEqual(status, 200, canvas)
        self.assertEqual(canvas.get("relations") or [], [])

        status, listed = self.request("GET", "/api/projects")
        source_settings = next(row for row in listed if int(row["id"]) == source["id"])
        self.assertEqual(parse_worldbuilding_md(source_settings.get("worldbuilding_md"))["locale"], "해안 도시 하버라인")
        status, source_char = self.request("GET", f"/api/characters/{source['a']}")
        self.assertEqual(source_char["character"]["author_notes_md"], "전작에만 남을 메모")
        self.assertFalse(character_import_analysis.is_tori_text(source_char["character"]["profile_md"]))

    def test_inherit_chronicle_maps_relations_and_null_scene_history(self) -> None:
        source = self._seed_source()
        status, created = self.request(
            "POST",
            "/api/projects",
            {
                "title": "속편 연대기",
                "main_genre": "판타지",
                "inherit_from_project_id": source["id"],
                "inherit_chronicle": True,
            },
        )
        self.assertEqual(status, 201, created)
        dest = int(created["id"])
        self.assertTrue(created.get("inherited_chronicle"))
        status, characters = self.request("GET", f"/api/projects/{dest}/characters")
        by_name = {row["name"]: row for row in characters}
        new_a = int(by_name["비비"]["id"])
        new_b = int(by_name["엔케"]["id"])
        self.assertNotEqual(new_a, source["a"])

        status, history = self.request("GET", f"/api/characters/{new_a}/trait-history")
        self.assertEqual(status, 200, history)
        entries = history.get("entries") or []
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]["scene_id"])
        self.assertEqual(entries[0]["field_name"], "profile_md")
        self.assertEqual(entries[0]["detected_content"], "검은 머리")

        status, items = self.request("GET", f"/api/projects/{dest}/items")
        status, item_hist = self.request("GET", f"/api/items/{items[0]['id']}/trait-history")
        self.assertEqual(status, 200, item_hist)
        self.assertEqual(item_hist["entries"][0]["scene_id"], None)

        status, canvas = self.request("GET", f"/api/projects/{dest}/character-canvas")
        self.assertEqual(status, 200, canvas)
        self.assertEqual(len(canvas.get("relations") or []), 1)
        rel = canvas["relations"][0]
        self.assertEqual(rel["status"], "confirmed")
        self.assertEqual(
            {int(rel["character_a_id"]), int(rel["character_b_id"])},
            {new_a, new_b},
        )
        self.assertEqual(rel["label"], "연인")
        self.assertTrue(all(row.get("x") is None and row.get("y") is None for row in canvas.get("characters") or []))

        status, source_canvas = self.request("GET", f"/api/projects/{source['id']}/character-canvas")
        self.assertEqual(len(source_canvas.get("relations") or []), 1)
        self.assertEqual(
            {int(source_canvas["relations"][0]["character_a_id"]), int(source_canvas["relations"][0]["character_b_id"])},
            {source["a"], source["b"]},
        )
