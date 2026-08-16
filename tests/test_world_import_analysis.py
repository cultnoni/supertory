"""Worldbuilding auto-analysis after document import (empty fill vs pending)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import gemini_client
import world_import_analysis


class WorldImportAnalysisUnitTests(unittest.TestCase):
    def test_parse_flat_and_nested_json(self) -> None:
        flat = world_import_analysis.parse_analysis_json(
            '{"reality":"현대 서울","era":"2020년대","factions":"길드"}'
        )
        self.assertEqual(flat["reality"], "현대 서울")
        self.assertEqual(flat["era"], "2020년대")
        self.assertEqual(flat["factions"], "길드")
        nested = world_import_analysis.parse_analysis_json(
            """```json
{"where_when":{"reality":"가상","era":"중세"},"unique_concept":{"special":"마법"}}
```"""
        )
        self.assertEqual(nested["reality"], "가상")
        self.assertEqual(nested["era"], "중세")
        self.assertEqual(nested["special"], "마법")

    def test_compose_parse_roundtrip(self) -> None:
        values = world_import_analysis.empty_world_values()
        values["reality"] = "현실에 가까운 가상"
        values["era"] = "근미래"
        values["legacy"] = "옛 메모"
        md = world_import_analysis.compose_worldbuilding_md(values)
        parsed = world_import_analysis.parse_worldbuilding_md(md)
        self.assertEqual(parsed["reality"], "현실에 가까운 가상")
        self.assertEqual(parsed["era"], "근미래")
        self.assertEqual(parsed["legacy"], "옛 메모")
        self.assertEqual(parsed["locale"], "")


class WorldImportAnalysisApplyTests(unittest.TestCase):
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
            connection.execute("INSERT INTO project(id, title) VALUES (1, '세계관 작품')")
            occupied = world_import_analysis.empty_world_values()
            occupied["reality"] = "이미 적어 둔 현실/가상"
            occupied["era"] = ""
            connection.execute(
                "UPDATE project SET worldbuilding_md = ? WHERE id = 1",
                (world_import_analysis.compose_worldbuilding_md(occupied),),
            )
            stats = world_import_analysis.apply_parsed_fields(
                connection,
                1,
                {
                    "reality": "토리가 쓴 현실/가상",
                    "era": "근미래",
                    "locale": "하버라인",
                    "special": "",
                },
            )
            self.assertEqual(stats["filled"], 2)
            self.assertEqual(stats["pending"], 1)
            md = connection.execute(
                "SELECT worldbuilding_md FROM project WHERE id = 1"
            ).fetchone()[0]
            values = world_import_analysis.parse_worldbuilding_md(md)
            self.assertEqual(values["reality"], "이미 적어 둔 현실/가상")
            self.assertEqual(values["era"], "근미래")
            self.assertEqual(values["locale"], "하버라인")
            pending = connection.execute(
                "SELECT section_name, field_name, analyzed_content FROM world_tori_analysis "
                "WHERE project_id = 1"
            ).fetchall()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["field_name"], "reality")
            self.assertEqual(pending[0]["section_name"], "where_when")
            self.assertEqual(pending[0]["analyzed_content"], "토리가 쓴 현실/가상")

            applied = world_import_analysis.apply_pending_field(connection, 1, "reality")
            after = world_import_analysis.parse_worldbuilding_md(applied)
            self.assertEqual(after["reality"], "토리가 쓴 현실/가상")
            leftover = connection.execute(
                "SELECT COUNT(*) FROM world_tori_analysis WHERE project_id = 1"
            ).fetchone()[0]
            self.assertEqual(leftover, 0)


class WorldImportAnalysisApiTests(unittest.TestCase):
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

    def test_apply_endpoint_and_outline_pending(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "세계관 분석", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        occupied = world_import_analysis.empty_world_values()
        occupied["era"] = "기존 시대"
        with app.database() as connection:
            connection.execute(
                "UPDATE project SET worldbuilding_md = ? WHERE id = ?",
                (world_import_analysis.compose_worldbuilding_md(occupied), project["id"]),
            )
            connection.execute(
                "INSERT INTO world_tori_analysis(project_id, section_name, field_name, analyzed_content) "
                "VALUES (?, 'where_when', 'era', '토리 시대')",
                (project["id"],),
            )
        status, outline = self.request("GET", f"/api/projects/{project['id']}/outline")
        self.assertEqual(status, 200)
        self.assertIn("era", outline["project"]["world_tori_analysis"])
        status, applied = self.request(
            "POST",
            f"/api/projects/{project['id']}/world-analysis/apply",
            {"field_name": "era"},
        )
        self.assertEqual(status, 200)
        values = world_import_analysis.parse_worldbuilding_md(applied["worldbuilding_md"])
        self.assertEqual(values["era"], "토리 시대")
        self.assertEqual(applied["world_tori_analysis"], {})

    @patch.object(app.time, "sleep", return_value=None)
    @patch.object(gemini_client, "is_configured", return_value=True)
    @patch.object(
        gemini_client,
        "generate_text",
        side_effect=[
            '{"characters":[{"name":"하나","short_description":"검사","profile_md":"키가 크다","strengths_md":"검술","weaknesses_md":""}]}',
            '{"reality":"가상 왕국","era":"중세","locale":"왕도","special":"마법","rules":"대가를 치른다"}',
        ],
    )
    def test_combined_job_fills_world_after_characters(self, generate, _configured, _sleep) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "불러오기 분석", "main_genre": "판타지"})
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
                "content_md": "하나는 검을 들고 왕도를 나섰다. 마법은 대가를 치러야 한다. " * 8,
                "status": "draft",
                "row_version": detail.get("row_version") or 1,
            },
        )
        snap = app.start_character_analysis_job(int(project["id"]), include_world=True)
        self.assertEqual(snap["status"], "running")
        worker = app._character_analysis_thread
        if worker is not None:
            worker.join(timeout=8)
        self.assertEqual(generate.call_count, 2)
        _sleep.assert_called()
        status, outline = self.request("GET", f"/api/projects/{project['id']}/outline")
        self.assertEqual(status, 200)
        values = world_import_analysis.parse_worldbuilding_md(
            outline["project"]["worldbuilding_md"]
        )
        self.assertEqual(values["reality"], "가상 왕국")
        self.assertEqual(values["era"], "중세")
        self.assertEqual(values["special"], "마법")
        done = app.character_analysis_snapshot()
        self.assertEqual(done["status"], "done")
        self.assertGreaterEqual(done["world_filled"], 3)
        self.assertGreaterEqual(done["created"], 1)


if __name__ == "__main__":
    unittest.main()
