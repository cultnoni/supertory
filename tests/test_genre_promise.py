"""장르문학 「장르 약속」 칸 (임시 DB)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import genre_track

ROOT = Path(__file__).resolve().parents[1]


class GenrePromiseHelperTests(unittest.TestCase):
    def test_fields_are_two_and_independent(self) -> None:
        self.assertEqual(genre_track.PROMISE_FIELDS, ("promise_broken", "promise_kept"))

    def test_promise_values_fall_back_to_empty(self) -> None:
        self.assertEqual(
            genre_track.promise_values({"promise_kept": "단서를 먼저 보여 준다"}),
            {"promise_broken": "", "promise_kept": "단서를 먼저 보여 준다"},
        )
        self.assertEqual(genre_track.promise_values(None), {"promise_broken": "", "promise_kept": ""})


class GenrePromiseApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, (json.loads(raw) if raw else {})

    def create(self, **fields: object) -> int:
        status, project = self.request("POST", "/api/projects", {"title": "작품", **fields})
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def genre_project(self, main_genre: str) -> int:
        return self.create(purpose="genre_literature", main_genre=main_genre, cluster_id="genre_literature")

    def test_migration_adds_both_columns(self) -> None:
        with app.database() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(project)").fetchall()}
        self.assertIn("promise_broken", columns)
        self.assertIn("promise_kept", columns)

    def test_new_genre_project_starts_empty_and_applies(self) -> None:
        project_id = self.genre_project("mystery")
        status, data = self.request("GET", f"/api/projects/{project_id}/genre-promise")
        self.assertEqual(status, 200, data)
        self.assertTrue(data["applies"])
        self.assertEqual(data["group"], "puzzle")
        self.assertEqual(data["promise_broken"], "")
        self.assertEqual(data["promise_kept"], "")

    def test_every_group_gets_the_boxes(self) -> None:
        for main, group in (
            ("mystery", "puzzle"), ("thriller", "puzzle"), ("sf", "world"),
            ("traditional", "world"), ("experimental", "style"),
        ):
            with self.subTest(main=main):
                project_id = self.genre_project(main)
                status, data = self.request("GET", f"/api/projects/{project_id}/genre-promise")
                self.assertEqual(status, 200, data)
                self.assertEqual(data["group"], group)
                self.assertTrue(data["applies"])

    def test_fields_save_independently(self) -> None:
        project_id = self.genre_project("sf")
        path = f"/api/projects/{project_id}/genre-promise"
        status, data = self.request("POST", path, {"promise_kept": "세계 규칙은 끝까지 지킨다"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["promise_kept"], "세계 규칙은 끝까지 지킨다")
        self.assertEqual(data["promise_broken"], "")
        status, data = self.request("POST", path, {"promise_broken": "열린 결말"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["promise_kept"], "세계 규칙은 끝까지 지킨다")
        self.assertEqual(data["promise_broken"], "열린 결말")
        status, data = self.request("GET", path)
        self.assertEqual(data["promise_broken"], "열린 결말")

    def test_empty_body_does_not_touch_values(self) -> None:
        project_id = self.genre_project("thriller")
        path = f"/api/projects/{project_id}/genre-promise"
        self.request("POST", path, {"promise_broken": "서술자가 독자를 속인다"})
        status, data = self.request("POST", path, {})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["promise_broken"], "서술자가 독자를 속인다")

    def test_value_is_capped(self) -> None:
        project_id = self.genre_project("mystery")
        status, data = self.request(
            "POST",
            f"/api/projects/{project_id}/genre-promise",
            {"promise_kept": "가" * (genre_track.PROMISE_FIELD_MAX_CHARS + 500)},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data["promise_kept"]), genre_track.PROMISE_FIELD_MAX_CHARS)

    def test_other_clusters_do_not_apply_and_reject_writes(self) -> None:
        project_id = self.create(purpose="web_novel", main_genre="fantasy", cluster_id="webnovel")
        path = f"/api/projects/{project_id}/genre-promise"
        status, data = self.request("GET", path)
        self.assertEqual(status, 200, data)
        self.assertFalse(data["applies"])
        status, data = self.request("POST", path, {"promise_kept": "무엇이든"})
        self.assertEqual(status, 400, data)
        self.assertIn("장르문학", json.dumps(data, ensure_ascii=False))

    def test_unset_subgenre_does_not_apply(self) -> None:
        project_id = self.genre_project("genre_lit")
        status, data = self.request("GET", f"/api/projects/{project_id}/genre-promise")
        self.assertEqual(status, 200, data)
        self.assertFalse(data["applies"])

    def test_missing_project_is_an_error(self) -> None:
        status, data = self.request("GET", "/api/projects/99999/genre-promise")
        self.assertEqual(status, 404, data)


class GenrePromiseUiContractTests(unittest.TestCase):
    def test_box_and_strings_exist_in_all_locales(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-settings-section="promise"', html)
        self.assertIn('data-promise-field="promise_broken"', html)
        self.assertIn('data-promise-field="promise_kept"', html)
        self.assertIn("function applyGenrePromiseUi", js)
        self.assertIn("/genre-promise", js)
        self.assertIn('"promise"', js)
        for lang in ("ko", "en", "es"):
            locale = (ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8")
            for key in (
                "app.장르_약속", "app.장르_약속_안내", "app.일부러_깬_약속", "app.지키고_싶은_약속",
                "app.장르_약속_깬_예시", "app.장르_약속_지킬_예시", "app.장르_약속을_적어_보세요",
            ):
                self.assertIn(f'"{key}"', locale, (lang, key))


if __name__ == "__main__":
    unittest.main()
