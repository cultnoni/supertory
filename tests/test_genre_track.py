"""장르문학 묶음 판별과 실험장르 두 줄(문학/웹소설) 선택 (가짜 Claude, 임시 DB)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app
import feedback_api
import genre_track
import literary_form
from feedback_pipeline.claude_client import (
    set_client_for_tests,
    set_configured_for_tests,
)
from test_feedback_pipeline import (
    MANUSCRIPT,
    PipelineFake,
    _default_consistency,
    _default_report,
)


def _project(main_genre: str, cluster_id: str = "genre_literature", **extra: object) -> dict:
    return {"cluster_id": cluster_id, "main_genre": main_genre, "sub_genre": "", **extra}


class GenreGroupTests(unittest.TestCase):
    def test_groups_follow_the_design(self) -> None:
        self.assertEqual(genre_track.genre_group(_project("mystery")), "puzzle")
        self.assertEqual(genre_track.genre_group(_project("thriller")), "puzzle")
        self.assertEqual(genre_track.genre_group(_project("sf")), "world")
        # traditional은 전통 문학이 아니라 정통판타지다
        self.assertEqual(genre_track.genre_group(_project("traditional")), "world")
        self.assertEqual(genre_track.genre_group(_project("experimental")), "style")
        self.assertEqual(genre_track.genre_group(_project("romance")), "relation")

    def test_unset_subgenre_and_other_clusters_have_no_group(self) -> None:
        self.assertIsNone(genre_track.genre_group(_project("genre_lit")))
        self.assertIsNone(genre_track.genre_group(_project("")))
        self.assertIsNone(genre_track.genre_group(_project("experimental", cluster_id="webnovel")))
        self.assertIsNone(genre_track.genre_group(_project("literary", cluster_id="general_literature")))

    def test_only_experimental_chooses_an_engine(self) -> None:
        self.assertTrue(genre_track.engine_choice_available(_project("experimental")))
        self.assertFalse(genre_track.engine_choice_available(_project("mystery")))
        self.assertFalse(genre_track.engine_choice_available(_project("literary", cluster_id="general_literature")))

    def test_resolve_engine(self) -> None:
        exp = _project("experimental")
        self.assertEqual(genre_track.resolve_engine(exp), "literary")
        self.assertEqual(genre_track.resolve_engine(exp, "web"), "web")
        self.assertEqual(genre_track.resolve_engine(exp, "웹소설"), "web")
        self.assertEqual(genre_track.resolve_engine(exp, "nonsense"), "literary")
        # 요청 값은 실험장르에서만 의미가 있다
        self.assertEqual(genre_track.resolve_engine(_project("mystery"), "literary"), "web")
        lit = _project("literary", cluster_id="general_literature")
        self.assertEqual(genre_track.resolve_engine(lit, "web"), "literary")


class LiteraryGateTests(unittest.TestCase):
    def test_experimental_is_a_literary_target_only_in_genre_literature(self) -> None:
        self.assertTrue(literary_form.is_literary_track_target("genre_literature", "", "experimental"))
        self.assertFalse(literary_form.is_literary_track_target("genre_literature", "", "mystery"))
        self.assertFalse(literary_form.is_literary_track_target("genre_literature", "", "traditional"))
        self.assertFalse(literary_form.is_literary_track_target("webnovel", "", "experimental"))

    def test_existing_general_literature_behaviour_is_unchanged(self) -> None:
        self.assertTrue(literary_form.is_literary_track_target("general_literature", "", "literary"))
        self.assertTrue(literary_form.is_literary_track_target("general_literature", "", "general_lit"))
        self.assertFalse(literary_form.is_literary_track_target("general_literature", "", "essay"))
        with self.assertRaises(ValueError):
            literary_form.resolve_required_literary_form(
                cluster_id="general_literature", main_genre="literary", sub_genre="", raw="",
            )

    def test_experimental_form_is_optional(self) -> None:
        self.assertIsNone(
            literary_form.resolve_required_literary_form(
                cluster_id="genre_literature", main_genre="experimental", sub_genre="", raw="",
            )
        )
        self.assertEqual(
            literary_form.resolve_required_literary_form(
                cluster_id="genre_literature", main_genre="experimental", sub_genre="", raw="short",
            ),
            "short",
        )
        # 선택하지 않으면 문학 줄은 꺼져 있다
        self.assertIsNone(literary_form.literary_track(_project("experimental")))
        self.assertEqual(literary_form.literary_track(_project("experimental", literary_form="long")), "long")

    def test_settings_keep_optional_form_blank(self) -> None:
        saved = literary_form.resolve_settings_literary_form(
            was_target=False, target=True, stored_form=None, body={}, form_optional=True,
        )
        self.assertIsNone(saved)
        with self.assertRaises(ValueError):
            literary_form.resolve_settings_literary_form(
                was_target=False, target=True, stored_form=None, body={}, form_optional=False,
            )


class ExperimentalEngineApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        feedback_api.reset_runtime_for_tests()
        set_configured_for_tests(True)
        set_client_for_tests(
            PipelineFake(consistency=_default_consistency(), report=_default_report())
        )
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        deadline = time.time() + 20
        while time.time() < deadline and feedback_api._active_count():
            time.sleep(0.05)
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        set_client_for_tests(None)
        set_configured_for_tests(None)
        feedback_api.reset_runtime_for_tests()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, (json.loads(raw) if raw else {})

    def create_scene(self, project_payload: dict) -> tuple[int, int]:
        status, project = self.request("POST", "/api/projects", project_payload)
        self.assertEqual(status, 201, project)
        project_id = int(project["id"])
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1장"})
        self.assertEqual(status, 201, chapter)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"})
        self.assertEqual(status, 201, scene)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {"title": "1화", "content_md": MANUSCRIPT, "status": "draft", "row_version": detail.get("row_version") or 1},
        )
        self.assertEqual(status, 200, saved)
        return project_id, int(scene["id"])

    def run_pipeline(self, created: object) -> str:
        status, run = self.request("GET", f"/api/feedback/runs/{int(created['run_id'])}")
        self.assertEqual(status, 200, run)
        return str(run.get("pipeline") or "")

    EXPERIMENTAL = {
        "title": "실험 작품",
        "purpose": "genre_literature",
        "main_genre": "experimental",
        "cluster_id": "genre_literature",
    }

    def test_experimental_project_can_be_created_without_form(self) -> None:
        status, project = self.request("POST", "/api/projects", self.EXPERIMENTAL)
        self.assertEqual(status, 201, project)
        self.assertIsNone(project.get("literary_form"))

    def test_experimental_project_keeps_a_chosen_form(self) -> None:
        status, project = self.request("POST", "/api/projects", {**self.EXPERIMENTAL, "literary_form": "short"})
        self.assertEqual(status, 201, project)
        self.assertEqual(project.get("literary_form"), "short")

    def test_default_run_without_form_uses_web_line(self) -> None:
        project_id, scene_id = self.create_scene(self.EXPERIMENTAL)
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/feedback/runs", {"scene_id": scene_id}
        )
        self.assertIn(status, (200, 201), created)
        self.assertEqual(self.run_pipeline(created), "")

    def test_explicit_literary_without_form_is_rejected(self) -> None:
        project_id, scene_id = self.create_scene(self.EXPERIMENTAL)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id, "engine": "literary"},
        )
        self.assertEqual(status, 400, created)
        self.assertIn("단편 또는 장편", json.dumps(created, ensure_ascii=False))

    def test_web_engine_ignores_the_chosen_form(self) -> None:
        project_id, scene_id = self.create_scene({**self.EXPERIMENTAL, "literary_form": "short"})
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id, "engine": "web"},
        )
        self.assertIn(status, (200, 201), created)
        self.assertEqual(self.run_pipeline(created), "")

    def test_literary_engine_with_form_runs_literature_pipeline(self) -> None:
        project_id, scene_id = self.create_scene({**self.EXPERIMENTAL, "literary_form": "short"})
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id, "engine": "literary"},
        )
        self.assertIn(status, (200, 201), created)
        self.assertEqual(self.run_pipeline(created), "literature_short")

    def test_other_genre_literature_projects_ignore_engine(self) -> None:
        project_id, scene_id = self.create_scene(
            {"title": "추리", "purpose": "genre_literature", "main_genre": "mystery", "cluster_id": "genre_literature"}
        )
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id, "engine": "literary"},
        )
        self.assertIn(status, (200, 201), created)
        self.assertEqual(self.run_pipeline(created), "")


if __name__ == "__main__":
    unittest.main()
