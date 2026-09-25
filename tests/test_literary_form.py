"""Literary track gate: 일반문학/순문학 only. Essay and other clusters stay unchanged."""

from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import app
import literary_form
import prompt_pipelines


class LiteraryFormUnitTests(unittest.TestCase):
    def test_track_ignores_sub_genre_length_tag(self) -> None:
        project = {
            "cluster_id": "general_literature",
            "main_genre": "literary",
            "sub_genre": "long",
            "literary_form": None,
        }
        self.assertTrue(
            literary_form.is_literary_track_target("general_literature", "long", "literary")
        )
        self.assertIsNone(literary_form.literary_track(project))

    def test_essay_and_other_clusters_are_off_the_track(self) -> None:
        self.assertFalse(
            literary_form.is_literary_track_target("general_literature", "tbd", "essay")
        )
        self.assertIsNone(
            literary_form.literary_track(
                {
                    "cluster_id": "general_literature",
                    "main_genre": "essay",
                    "sub_genre": "tbd",
                    "literary_form": "short",
                }
            )
        )
        self.assertFalse(
            literary_form.is_literary_track_target("webnovel", "일반문학", "fantasy")
        )
        self.assertEqual(literary_form.public_literary_fields({
            "cluster_id": "webnovel",
            "main_genre": "fantasy",
            "sub_genre": "regression",
            "literary_form": "short",
        }), {})

    def test_prompt_pipeline_id_unchanged_for_literature(self) -> None:
        self.assertEqual(prompt_pipelines.prompt_pipeline_id("general_literature"), "webnovel")
        self.assertEqual(prompt_pipelines.GENERAL_LITERATURE_PIPELINE, "general_literature")


class LiteraryFormApiTests(unittest.TestCase):
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
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        if not raw:
            return response.status, {}
        return response.status, json.loads(raw.decode("utf-8"))

    def stored_form(self, project_id: int) -> str | None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT literary_form, style_narration, style_sentence FROM project WHERE id = ?",
                (project_id,),
            ).fetchone()
        return row

    def test_create_webnovel_omits_literary_keys(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "웹소설",
                "purpose": "web_novel",
                "cluster_id": "webnovel",
                "main_genre": "fantasy",
                "sub_genre": "regression",
                "literary_form": "short",
            },
        )
        self.assertEqual(status, 201, project)
        self.assertNotIn("literary_form", project)
        self.assertNotIn("style_narration", project)
        row = self.stored_form(project["id"])
        self.assertIsNone(row["literary_form"])

    def test_create_literary_requires_form(self) -> None:
        status, missing = self.request(
            "POST",
            "/api/projects",
            {
                "title": "일반문학",
                "purpose": "literature",
                "cluster_id": "general_literature",
                "main_genre": "general_lit",
                "sub_genre": "mid",
            },
        )
        self.assertEqual(status, 400, missing)
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "일반문학",
                "purpose": "literature",
                "cluster_id": "general_literature",
                "main_genre": "general_lit",
                "sub_genre": "mid",
                "literary_form": "short",
            },
        )
        self.assertEqual(status, 201, project)
        self.assertEqual(project["literary_form"], "short")
        self.assertEqual(project["style_choice"], "")

    def test_create_essay_ignores_form(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "에세이",
                "purpose": "literature",
                "cluster_id": "general_literature",
                "main_genre": "essay",
                "sub_genre": "tbd",
                "literary_form": "long",
            },
        )
        self.assertEqual(status, 201, project)
        self.assertNotIn("literary_form", project)
        row = self.stored_form(project["id"])
        self.assertIsNone(row["literary_form"])

    def test_settings_keep_legacy_null_and_save_one_style_field(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "순문학",
                "purpose": "literature",
                "cluster_id": "general_literature",
                "main_genre": "literary",
                "sub_genre": "long",
                "literary_form": "long",
            },
        )
        self.assertEqual(status, 201, project)
        project_id = project["id"]
        with app.database() as connection:
            connection.execute(
                "UPDATE project SET literary_form = NULL, description_md = '시놉' WHERE id = ?",
                (project_id,),
            )
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/settings",
            {"synopsis_md": "시놉시스 유지"},
        )
        self.assertEqual(status, 200, saved)
        self.assertIsNone(saved["literary_form"])
        self.assertEqual(saved["synopsis_md"], "시놉시스 유지")
        row = self.stored_form(project_id)
        self.assertIsNone(row["literary_form"])

        status, entering = self.request(
            "POST",
            "/api/projects",
            {
                "title": "에세이에서",
                "purpose": "literature",
                "cluster_id": "general_literature",
                "main_genre": "essay",
                "sub_genre": "tbd",
            },
        )
        self.assertEqual(status, 201, entering)
        status, blocked = self.request(
            "POST",
            f"/api/projects/{entering['id']}/settings",
            {"main_genre": "general_lit", "sub_genre": "mid"},
        )
        self.assertEqual(status, 400, blocked)

        status, styled = self.request(
            "POST",
            f"/api/projects/{project_id}/settings",
            {"style_narration": "1인칭 현재"},
        )
        self.assertEqual(status, 200, styled)
        self.assertEqual(styled["style_narration"], "1인칭 현재")
        self.assertEqual(styled["style_sentence"], "")
        self.assertEqual(styled["synopsis_md"], "시놉시스 유지")
        self.assertIsNone(styled["literary_form"])
        row = self.stored_form(project_id)
        self.assertEqual(row["style_narration"], "1인칭 현재")
        self.assertEqual(row["style_sentence"], "")

        status, outline = self.request("GET", f"/api/projects/{project_id}/outline")
        self.assertEqual(status, 200, outline)
        self.assertEqual(outline["project"]["style_narration"], "1인칭 현재")
        self.assertIsNone(outline["project"]["literary_form"])

        status, listed = self.request("GET", "/api/projects")
        web = next(item for item in listed if item["title"] == "에세이에서")
        self.assertNotIn("literary_form", web)
        lit = next(item for item in listed if item["id"] == project_id)
        self.assertIn("literary_form", lit)
        self.assertIsNone(lit["literary_form"])
