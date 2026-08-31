"""Cross-reference search across characters, items, world, and relations."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

import app
import settings_search
from world_import_analysis import compose_worldbuilding_md


class SettingsSearchMatchTests(unittest.TestCase):
    def test_case_and_spacing_are_lenient(self) -> None:
        self.assertTrue(settings_search.text_matches("빨간 머리", "빨간머리"))
        self.assertTrue(settings_search.text_matches("Harbor Line", "harborline"))
        self.assertFalse(settings_search.text_matches("하버라인", "서킷"))

    def test_snippet_keeps_nearby_context(self) -> None:
        snippet = settings_search.make_snippet("달빛을 먹는 검은 칼날", "먹는")
        self.assertIn("먹는", snippet)
        self.assertLessEqual(len(snippet), 80)


class SettingsSearchHttpTests(unittest.TestCase):
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

    def _project(self, title: str = "검색 작품") -> int:
        status, project = self.request("POST", "/api/projects", {"title": title, "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def _character(
        self,
        project_id: int,
        name: str,
        *,
        profile: str = "",
        summary: str = "",
        strengths: str = "",
        weaknesses: str = "",
        aliases: list[str] | None = None,
    ) -> int:
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
            "profile_md": profile,
            "short_description": summary,
            "strengths_md": strengths,
            "weaknesses_md": weaknesses,
            "row_version": detail["character"]["row_version"],
        }
        if aliases is not None:
            body["aliases"] = aliases
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
        status, detail = self.request("GET", f"/api/items/{item_id}")
        self.assertEqual(status, 200, detail)
        body = {
            "name": name,
            "description": description,
            "row_version": detail["item"]["row_version"],
        }
        if aliases is not None:
            body["aliases"] = aliases
        status, _ = self.request("PUT", f"/api/items/{item_id}", body)
        self.assertEqual(status, 200)
        return item_id

    def test_empty_query_returns_empty_groups(self) -> None:
        pid = self._project()
        status, data = self.request("GET", f"/api/projects/{pid}/settings-search?q=")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["characters"], [])
        self.assertEqual(data["items"], [])
        self.assertEqual(data["world"], [])
        self.assertEqual(data["relations"], [])

    def test_search_hits_character_item_world_relation_and_stays_in_project(self) -> None:
        pid = self._project("본 작품")
        other = self._project("다른 작품")
        a = self._character(
            pid,
            "비비",
            profile="[외모]\n빨간 머리\n[성격]\n차가움\n[관계]\n엔케의 연인",
            summary="달빛 아래의 후계자",
            strengths="흑염검",
            weaknesses="고독",
            aliases=["흑염의 후계"],
        )
        b = self._character(pid, "엔케", profile="[관계]\n비비의 주인")
        other_bibi = self._character(other, "비비", aliases=["흑염의 후계"])
        self._item(pid, "흑염검", "달빛을 먹는 검은 칼날", aliases=["흑검"])
        self._item(other, "흑염검", "달빛을 먹는 검은 칼날")
        world = compose_worldbuilding_md({"locale": "해안 도시 하버라인", "heritage": "검은 비의 기원"})
        status, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/settings",
            {"worldbuilding_md": world},
        )
        self.assertEqual(status, 200)
        status, _ = self.request(
            "PUT",
            f"/api/projects/{other}/settings",
            {"worldbuilding_md": compose_worldbuilding_md({"locale": "하버라인"})},
        )
        self.assertEqual(status, 200)
        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/character-relations",
            {"character_a_id": a, "character_b_id": b, "label": "연인"},
        )
        self.assertEqual(status, 201, created)
        status, _ = self.request(
            "POST",
            f"/api/projects/{other}/character-relations",
            {
                "character_a_id": self._character(other, "서윤"),
                "character_b_id": self._character(other, "리아"),
                "label": "연인",
            },
        )
        self.assertEqual(status, 201)

        status, by_name = self.request("GET", f"/api/projects/{pid}/settings-search?q={quote('비비')}")
        self.assertEqual(status, 200, by_name)
        by_name_ids = {hit["id"] for hit in by_name["characters"]}
        self.assertIn(a, by_name_ids)
        self.assertNotIn(other_bibi, by_name_ids)
        name_hit = next(hit for hit in by_name["characters"] if hit["id"] == a)
        self.assertEqual(name_hit["field"], "name")

        status, by_alias = self.request(
            "GET", f"/api/projects/{pid}/settings-search?q={quote('흑염의 후계')}"
        )
        self.assertEqual(status, 200, by_alias)
        self.assertEqual({hit["id"] for hit in by_alias["characters"]}, {a})
        self.assertEqual(by_alias["characters"][0]["field"], "alias")

        status, by_item = self.request(
            "GET", f"/api/projects/{pid}/settings-search?q={quote('달빛을 먹는')}"
        )
        self.assertEqual(status, 200, by_item)
        self.assertEqual(len(by_item["items"]), 1)
        self.assertEqual(by_item["items"][0]["field"], "description")
        self.assertIn("달빛", by_item["items"][0]["snippet"])

        status, by_world = self.request(
            "GET", f"/api/projects/{pid}/settings-search?q={quote('하버라인')}"
        )
        self.assertEqual(status, 200, by_world)
        self.assertTrue(by_world["world"])
        self.assertEqual(by_world["world"][0]["field"], "locale")
        self.assertNotIn(
            "검은 비",
            " ".join(hit.get("snippet") or "" for hit in by_world["world"]),
        )

        status, heritage = self.request(
            "GET", f"/api/projects/{pid}/settings-search?q={quote('검은 비')}"
        )
        self.assertEqual(status, 200, heritage)
        self.assertEqual({hit["field"] for hit in heritage["world"]}, {"heritage"})

        status, by_rel = self.request(
            "GET", f"/api/projects/{pid}/settings-search?q={quote('연인')}"
        )
        self.assertEqual(status, 200, by_rel)
        self.assertEqual(len(by_rel["relations"]), 1)
        self.assertEqual(by_rel["relations"][0]["field"], "label")
        self.assertEqual(
            {by_rel["relations"][0]["character_a_id"], by_rel["relations"][0]["character_b_id"]},
            {a, b},
        )

        status, compact = self.request(
            "GET", f"/api/projects/{pid}/settings-search?q={quote('빨간머리')}"
        )
        self.assertEqual(status, 200, compact)
        self.assertEqual({hit["id"] for hit in compact["characters"]}, {a})
        self.assertEqual(compact["characters"][0]["field"], "appearance")

        status, miss = self.request(
            "GET", f"/api/projects/{pid}/settings-search?q={quote('없는단어xyz')}"
        )
        self.assertEqual(status, 200, miss)
        self.assertEqual(miss["characters"], [])
        self.assertEqual(miss["items"], [])
        self.assertEqual(miss["world"], [])
        self.assertEqual(miss["relations"], [])

        status, other_hits = self.request(
            "GET", f"/api/projects/{other}/settings-search?q={quote('하버라인')}"
        )
        self.assertEqual(status, 200, other_hits)
        self.assertTrue(other_hits["world"])
        self.assertEqual(other_hits["characters"], [])
        self.assertEqual(other_hits["items"], [])
