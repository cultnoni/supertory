"""Character auto-analysis after document import (empty fill vs pending)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import character_import_analysis
import gemini_client


class CharacterImportAnalysisUnitTests(unittest.TestCase):
    def test_parse_json_and_compose_profile(self) -> None:
        raw = """```json
{"characters":[
  {"name":"서윤","role":"주인공","short_description":"밤의 서점 주인",
   "appearance":"검은 머리","personality":"차분하다","speech":"존댓말",
   "relations":"지은과 친구","strengths_md":"관찰력","weaknesses_md":"고집이 셈"}
]}
```"""
        parsed = character_import_analysis.parse_analysis_json(raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "서윤")
        self.assertEqual(parsed[0]["role"], "protagonist")
        self.assertIn("외모", parsed[0]["fields"]["profile_md"])
        self.assertIn("차분하다", parsed[0]["fields"]["profile_md"])
        self.assertEqual(parsed[0]["fields"]["strengths_md"], "관찰력")

    def test_skips_generic_names(self) -> None:
        parsed = character_import_analysis.parse_analysis_json(
            '{"characters":[{"name":"그","short_description":"남자"},{"name":"지은","short_description":"친구"}]}'
        )
        self.assertEqual([item["name"] for item in parsed], ["지은"])

    def test_mark_tori_text(self) -> None:
        self.assertEqual(character_import_analysis.mark_tori_text("소개"), "〔토리〕 소개")
        self.assertEqual(
            character_import_analysis.mark_tori_text("〔토리〕 소개"),
            "〔토리〕 소개",
        )
        self.assertTrue(character_import_analysis.is_tori_text("〔토리〕 소개"))
        self.assertFalse(character_import_analysis.is_tori_text("기존 소개"))

    def test_infer_prompt_uses_plot_when_no_manuscript(self) -> None:
        system, user = character_import_analysis.build_analysis_prompt(
            "",
            ["서윤"],
            plot_context="[줄거리]\n밤의 서점 주인이 편지를 받는다.",
            infer=True,
        )
        self.assertIn("줄거리만", user)
        self.assertIn("서윤", user)
        self.assertIn("비어 있는 칸", system)
        self.assertIn("[원고]\n(없음)", user)


class CharacterImportAnalysisApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()

    def tearDown(self) -> None:
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_fills_empty_and_keeps_occupied(self) -> None:
        with app.database() as connection:
            connection.execute("INSERT INTO project(id, title) VALUES (1, '분석 작품')")
            connection.execute(
                "INSERT INTO character(id, project_id, name, role, sort_order, "
                "short_description, profile_md, strengths_md, weaknesses_md) "
                "VALUES (10, 1, '서윤', 'supporting', 0, '기존 소개', '', '', '')"
            )
            parsed = [
                {
                    "name": "서윤",
                    "role": "protagonist",
                    "fields": {
                        "short_description": "토리가 쓴 소개",
                        "profile_md": "토리 인물 설정",
                        "strengths_md": "빠른 손",
                        "weaknesses_md": "",
                    },
                },
                {
                    "name": "지은",
                    "role": "supporting",
                    "fields": {
                        "short_description": "새 인물 소개",
                        "profile_md": "새 인물 설정",
                        "strengths_md": "",
                        "weaknesses_md": "겁이 많음",
                    },
                },
            ]
            stats = character_import_analysis.apply_parsed_characters(connection, 1, parsed)
            self.assertEqual(stats["created"], 1)
            self.assertEqual(stats["matched"], 1)
            self.assertGreaterEqual(stats["filled"], 3)
            self.assertEqual(stats["pending"], 1)
            seoyoon = connection.execute(
                "SELECT short_description, profile_md, strengths_md FROM character "
                "WHERE name = '서윤'"
            ).fetchone()
            self.assertEqual(seoyoon["short_description"], "기존 소개")
            self.assertEqual(seoyoon["profile_md"], "〔토리〕 토리 인물 설정")
            self.assertEqual(seoyoon["strengths_md"], "〔토리〕 빠른 손")
            pending = connection.execute(
                "SELECT field_name, analyzed_content FROM character_tori_analysis "
                "WHERE character_id = 10"
            ).fetchall()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["field_name"], "short_description")
            self.assertEqual(pending[0]["analyzed_content"], "〔토리〕 토리가 쓴 소개")
            jieun = connection.execute(
                "SELECT short_description, weaknesses_md FROM character WHERE name = '지은'"
            ).fetchone()
            self.assertEqual(jieun["short_description"], "〔토리〕 새 인물 소개")
            self.assertEqual(jieun["weaknesses_md"], "〔토리〕 겁이 많음")

            character_import_analysis.apply_pending_field(connection, 10, "short_description")
            after = connection.execute(
                "SELECT short_description FROM character WHERE id = 10"
            ).fetchone()[0]
            self.assertEqual(after, "〔토리〕 토리가 쓴 소개")
            leftover = connection.execute(
                "SELECT COUNT(*) FROM character_tori_analysis WHERE character_id = 10"
            ).fetchone()[0]
            self.assertEqual(leftover, 0)


class CharacterImportAnalysisApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.reset_character_analysis_state()
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
        worker = app._character_analysis_thread
        if worker is not None and worker.is_alive():
            worker.join(timeout=8)
        app.reset_character_analysis_state()
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_apply_endpoint_and_detail_pending(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "인물 분석", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        status, character = self.request(
            "POST", f"/api/projects/{project['id']}/characters", {"name": "서윤"}
        )
        self.assertEqual(status, 201)
        with app.database() as connection:
            connection.execute(
                "UPDATE character SET short_description = '기존 소개' WHERE id = ?",
                (character["id"],),
            )
            connection.execute(
                "INSERT INTO character_tori_analysis(character_id, field_name, analyzed_content) "
                "VALUES (?, 'short_description', '토리 소개')",
                (character["id"],),
            )
        status, detail = self.request("GET", f"/api/characters/{character['id']}")
        self.assertEqual(status, 200)
        self.assertIn("short_description", detail["character"]["tori_analysis"])
        status, applied = self.request(
            "POST",
            f"/api/characters/{character['id']}/tori-analysis/apply",
            {"field_name": "short_description"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(applied["character"]["short_description"], "토리 소개")
        self.assertEqual(applied["character"]["tori_analysis"], {})

    @patch.object(gemini_client, "is_configured", return_value=True)
    @patch.object(
        gemini_client,
        "generate_text",
        return_value='{"characters":[{"name":"하나","short_description":"검사","profile_md":"키가 크다","strengths_md":"검술","weaknesses_md":""}]}',
    )
    def test_job_creates_character(self, _generate, _configured) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "자동 생성", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        status, chapter = self.request("POST", f"/api/projects/{project['id']}/chapters", {"title": "1장"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"})
        self.assertEqual(status, 201)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": "1화",
                "content_md": "하나는 검을 들고 성문을 나섰다. " * 8,
                "status": "draft",
                "row_version": detail.get("row_version") or 1,
            },
        )
        status, job = self.request("POST", f"/api/projects/{project['id']}/character-analysis", {})
        self.assertEqual(status, 200)
        worker = app._character_analysis_thread
        if worker is not None:
            worker.join(timeout=8)
        status, characters = self.request("GET", f"/api/projects/{project['id']}/characters")
        self.assertEqual(status, 200)
        names = [item["name"] for item in characters]
        self.assertIn("하나", names)

    @patch.object(gemini_client, "is_configured", return_value=True)
    @patch.object(
        gemini_client,
        "generate_text",
        return_value='{"characters":[{"name":"린","short_description":"왕녀","appearance":"은발","personality":"차갑다","speech":"존댓말","relations":"근위와 대립","strengths_md":"검술","weaknesses_md":"고립"}]}',
    )
    def test_infer_fills_from_synopsis_without_manuscript(self, generate, _configured) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "줄거리만", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        with app.database() as connection:
            connection.execute(
                "UPDATE project SET description_md = ? WHERE id = ?",
                ("왕녀 린이 성을 떠나 검을 든다. 근위 카엘이 뒤를 쫓는다.", project["id"]),
            )
        status, job = self.request(
            "POST",
            f"/api/projects/{project['id']}/character-analysis",
            {"infer": True, "include_characters": True, "include_world": False},
        )
        self.assertEqual(status, 200)
        worker = app._character_analysis_thread
        if worker is not None:
            worker.join(timeout=8)
        self.assertTrue(generate.called)
        prompt = str(generate.call_args.kwargs.get("prompt") or generate.call_args[0][0])
        self.assertIn("줄거리", prompt)
        status, characters = self.request("GET", f"/api/projects/{project['id']}/characters")
        self.assertEqual(status, 200)
        self.assertEqual(characters[0]["name"], "린")
        self.assertTrue(str(characters[0]["short_description"]).startswith("〔토리〕"))

    def test_ui_has_tori_fill_buttons(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        self.assertGreaterEqual(html.count("토리야 작성해줘"), 4)
        self.assertIn('data-tori-fill="world"', html)
        self.assertIn('data-tori-fill="characters"', html)
        self.assertIn("startToriSettingsFill", js)
        self.assertIn("is-tori-draft", js)


if __name__ == "__main__":
    unittest.main()
