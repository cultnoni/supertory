"""웹소설 「장르 클리셰」 칸 (임시 DB)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import webnovel_genre_track

ROOT = Path(__file__).resolve().parents[1]


class WebnovelClicheHelperTests(unittest.TestCase):
    def test_fields_are_two_and_independent(self) -> None:
        self.assertEqual(webnovel_genre_track.CLICHE_FIELDS, ("cliche_used", "cliche_avoid"))

    def test_cliche_values_fall_back_to_empty(self) -> None:
        self.assertEqual(
            webnovel_genre_track.cliche_values({"cliche_avoid": "나쁜 남자 서사는 지양"}),
            {"cliche_used": "", "cliche_avoid": "나쁜 남자 서사는 지양"},
        )
        self.assertEqual(webnovel_genre_track.cliche_values(None), {"cliche_used": "", "cliche_avoid": ""})

    def test_genre_group_maps_every_webnovel_main(self) -> None:
        for main, sub, group in (
            ("romance", "", "web_romance"),
            ("romfant", "", "web_romance"),
            ("fantasy", "male", "web_fantasy_male"),
            ("martial", "", "web_fantasy_male"),
            ("historical", "", "web_fantasy_male"),
            ("sports", "", "web_fantasy_male"),
            ("fantasy", "female", "web_fantasy_female"),
            ("female_fantasy", "", "web_fantasy_female"),
        ):
            with self.subTest(main=main, sub=sub):
                project = {"cluster_id": "webnovel", "main_genre": main, "sub_genre": sub}
                self.assertEqual(webnovel_genre_track.genre_group(project), group)

    def test_fantasy_without_sub_genre_is_none(self) -> None:
        self.assertIsNone(
            webnovel_genre_track.genre_group({"cluster_id": "webnovel", "main_genre": "fantasy", "sub_genre": ""})
        )

    def test_other_clusters_and_unmapped_main_are_none(self) -> None:
        self.assertIsNone(webnovel_genre_track.genre_group({"cluster_id": "genre_literature", "main_genre": "romance"}))
        self.assertIsNone(webnovel_genre_track.genre_group({"cluster_id": "webnovel", "main_genre": ""}))
        self.assertIsNone(webnovel_genre_track.genre_group(None))


class WebnovelClicheApiTests(unittest.TestCase):
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

    def webnovel_project(self, main_genre: str, sub_genre: str = "") -> int:
        return self.create(
            purpose="web_novel", main_genre=main_genre, sub_genre=sub_genre, cluster_id="webnovel"
        )

    def test_migration_adds_both_columns(self) -> None:
        with app.database() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(project)").fetchall()}
        self.assertIn("cliche_used", columns)
        self.assertIn("cliche_avoid", columns)

    def test_new_webnovel_project_starts_empty_and_applies(self) -> None:
        project_id = self.webnovel_project("fantasy", "male")
        status, data = self.request("GET", f"/api/projects/{project_id}/webnovel-cliche")
        self.assertEqual(status, 200, data)
        self.assertTrue(data["applies"])
        self.assertEqual(data["group"], "web_fantasy_male")
        self.assertEqual(data["cliche_used"], "")
        self.assertEqual(data["cliche_avoid"], "")

    def test_every_group_gets_the_boxes(self) -> None:
        for main, group in (
            ("romance", "web_romance"), ("romfant", "web_romance"),
            ("martial", "web_fantasy_male"), ("sports", "web_fantasy_male"),
            ("female_fantasy", "web_fantasy_female"),
        ):
            with self.subTest(main=main):
                project_id = self.webnovel_project(main)
                status, data = self.request("GET", f"/api/projects/{project_id}/webnovel-cliche")
                self.assertEqual(status, 200, data)
                self.assertEqual(data["group"], group)
                self.assertTrue(data["applies"])

    def test_fields_save_independently(self) -> None:
        project_id = self.webnovel_project("romance")
        path = f"/api/projects/{project_id}/webnovel-cliche"
        status, data = self.request("POST", path, {"cliche_used": "계약 연애는 의도"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["cliche_used"], "계약 연애는 의도")
        self.assertEqual(data["cliche_avoid"], "")
        status, data = self.request("POST", path, {"cliche_avoid": "나쁜 남자 서사는 지양"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["cliche_used"], "계약 연애는 의도")
        self.assertEqual(data["cliche_avoid"], "나쁜 남자 서사는 지양")
        status, data = self.request("GET", path)
        self.assertEqual(data["cliche_used"], "계약 연애는 의도")

    def test_empty_body_does_not_touch_values(self) -> None:
        project_id = self.webnovel_project("martial")
        path = f"/api/projects/{project_id}/webnovel-cliche"
        self.request("POST", path, {"cliche_used": "회귀는 의도"})
        status, data = self.request("POST", path, {})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["cliche_used"], "회귀는 의도")

    def test_value_is_capped(self) -> None:
        project_id = self.webnovel_project("fantasy", "male")
        status, data = self.request(
            "POST",
            f"/api/projects/{project_id}/webnovel-cliche",
            {"cliche_avoid": "가" * (webnovel_genre_track.CLICHE_FIELD_MAX_CHARS + 500)},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data["cliche_avoid"]), webnovel_genre_track.CLICHE_FIELD_MAX_CHARS)

    def test_other_clusters_do_not_apply_and_reject_writes(self) -> None:
        project_id = self.create(purpose="genre_literature", main_genre="mystery", cluster_id="genre_literature")
        path = f"/api/projects/{project_id}/webnovel-cliche"
        status, data = self.request("GET", path)
        self.assertEqual(status, 200, data)
        self.assertFalse(data["applies"])
        status, data = self.request("POST", path, {"cliche_avoid": "무엇이든"})
        self.assertEqual(status, 400, data)
        self.assertIn("웹소설", json.dumps(data, ensure_ascii=False))

    def test_unmapped_main_genre_does_not_apply(self) -> None:
        project_id = self.webnovel_project("web_novel_misc")
        status, data = self.request("GET", f"/api/projects/{project_id}/webnovel-cliche")
        self.assertEqual(status, 200, data)
        self.assertFalse(data["applies"])

    def test_missing_project_is_an_error(self) -> None:
        status, data = self.request("GET", "/api/projects/99999/webnovel-cliche")
        self.assertEqual(status, 404, data)


class WebnovelClicheUiContractTests(unittest.TestCase):
    def test_box_and_strings_exist_in_all_locales(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-settings-section="cliche"', html)
        self.assertIn('data-cliche-field="cliche_used"', html)
        self.assertIn('data-cliche-field="cliche_avoid"', html)
        self.assertIn("function applyWebnovelClicheUi", js)
        self.assertIn("function appendWebnovelClicheDraft", js)
        self.assertIn('section === "cliche"', js)
        self.assertIn("/webnovel-cliche", js)
        self.assertIn('"cliche"', js)
        for lang in ("ko", "en", "es"):
            locale = (ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8")
            for key in (
                "app.장르_클리셰", "app.장르_클리셰_안내", "app.의도적으로_쓴_클리셰", "app.피하고_싶은_클리셰",
                "app.장르_클리셰_쓴_예시", "app.장르_클리셰_피할_예시", "app.장르_클리셰를_적어_보세요",
            ):
                self.assertIn(f'"{key}"', locale, (lang, key))


if __name__ == "__main__":
    unittest.main()
