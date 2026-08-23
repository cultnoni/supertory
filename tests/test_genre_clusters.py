# -*- coding: utf-8 -*-
"""Genre cluster mapping, persistence, and feature gating."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import genre_clusters


class GenreClusterLogicTests(unittest.TestCase):
    def test_cluster_catalog_matches_spec(self) -> None:
        clusters = {item["id"]: item for item in genre_clusters.load_clusters()}
        self.assertEqual(
            set(clusters),
            {"webnovel", "genre_literature", "general_literature", "fairytale", "locked"},
        )
        self.assertEqual(clusters["webnovel"]["status"], "active")
        self.assertEqual(clusters["locked"]["status"], "locked")
        self.assertIn("로판", clusters["webnovel"]["sub_genres"])
        self.assertEqual(
            clusters["genre_literature"]["sub_genres"],
            ["추리/미스터리", "스릴러", "SF"],
        )

    def test_infer_webnovel_and_genre_literature_keep_all_features(self) -> None:
        self.assertEqual(
            genre_clusters.infer_cluster_id("web_novel", "romance", "romfant"),
            "webnovel",
        )
        self.assertEqual(
            genre_clusters.infer_cluster_id("general_novel", "mystery", "honkaku"),
            "genre_literature",
        )
        self.assertEqual(
            genre_clusters.infer_cluster_id("general_novel", "sf", "space"),
            "genre_literature",
        )
        self.assertEqual(
            genre_clusters.infer_cluster_id("general_novel", "thriller", "psycho"),
            "genre_literature",
        )
        self.assertEqual(genre_clusters.get_visible_features("webnovel"), list(genre_clusters.ALL_CLUSTER_FEATURE_IDS))
        self.assertEqual(
            genre_clusters.get_visible_features("genre_literature"),
            list(genre_clusters.ALL_CLUSTER_FEATURE_IDS),
        )

    def test_general_literature_and_fairytale_hide_expected_features(self) -> None:
        self.assertEqual(
            genre_clusters.infer_cluster_id("general_novel", "contemporary", "daily"),
            "general_literature",
        )
        self.assertEqual(genre_clusters.infer_cluster_id("essay", "other", "tbd"), "general_literature")
        self.assertEqual(genre_clusters.infer_cluster_id("fairy_tale", "preschool", ""), "fairytale")
        self.assertFalse(genre_clusters.is_feature_visible("baits", "general_literature"))
        self.assertFalse(genre_clusters.is_feature_visible("summarize", "general_literature"))
        self.assertFalse(genre_clusters.is_feature_visible("reader_comments", "general_literature"))
        self.assertTrue(genre_clusters.is_feature_visible("baits", "fairytale"))
        self.assertFalse(genre_clusters.is_feature_visible("foreshadow", "fairytale"))
        self.assertFalse(genre_clusters.is_feature_visible("reader_debate", "fairytale"))
        self.assertTrue(genre_clusters.is_feature_visible("summarize", "fairytale"))

    def test_legacy_novel_purpose_and_locked_purposes(self) -> None:
        self.assertEqual(genre_clusters.infer_cluster_id("novel", "romance", ""), "general_literature")
        self.assertEqual(genre_clusters.infer_cluster_id("short_story", "", ""), "locked")
        self.assertTrue(genre_clusters.is_feature_visible("baits", "locked"))

    def test_stored_cluster_wins_over_genre(self) -> None:
        self.assertEqual(
            genre_clusters.infer_cluster_id("general_novel", "romance", "", "webnovel"),
            "webnovel",
        )

    def test_map_cluster_subgenre(self) -> None:
        mapped = genre_clusters.map_cluster_subgenre("webnovel", "romfant")
        self.assertEqual(mapped, ("web_novel", "romance", "romfant"))
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "mystery_detective"),
            ("general_novel", "mystery", "honkaku"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "detective"),
            ("general_novel", "mystery", "honkaku"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "mystery"),
            ("general_novel", "mystery", "honkaku"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "thriller"),
            ("general_novel", "thriller", "psycho"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "sf"),
            ("general_novel", "sf", "space"),
        )

    def test_genre_detail_allowed_values_and_labels(self) -> None:
        self.assertEqual(
            genre_clusters.normalize_genre_detail("romance", "modern", "historical"),
            "historical",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("romance", "romfant", "oriental_romfant"),
            "oriental_romfant",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "male", "alt_history"),
            "alt_history",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "male", "murim"),
            "murim",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "male", "urban"),
            "urban",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "male", "traditional"),
            "traditional",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "male", "hidden_world"),
            "hidden_world",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "male", "sports"),
            "sports",
        )
        self.assertEqual(genre_clusters.normalize_genre_detail("fantasy", "female", "alt_history"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("romance", "modern", "alt_history"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("fantasy", "female", "murim"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("fantasy", "female", "urban"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("romance", "modern", "traditional"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("fantasy", "female", "hidden_world"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("romance", "modern", "hidden_world"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("fantasy", "female", "sports"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("romance", "modern", "sports"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("romance", "modern", ""), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("mystery", "honkaku", "historical"), "")
        self.assertEqual(genre_clusters.genre_detail_label("historical"), "사극")
        self.assertEqual(genre_clusters.genre_detail_label("oriental_romfant"), "동양로판")
        self.assertEqual(genre_clusters.genre_detail_label("alt_history"), "대체역사")
        self.assertEqual(genre_clusters.genre_detail_label("murim"), "무협")
        self.assertEqual(genre_clusters.genre_detail_label("urban"), "현대판타지")
        self.assertEqual(genre_clusters.genre_detail_label("hidden_world"), "어반판타지")
        self.assertEqual(genre_clusters.genre_detail_label("traditional"), "정통판타지")
        self.assertEqual(genre_clusters.genre_detail_label("sports"), "스포츠물")
        self.assertEqual(genre_clusters.genre_detail_label(""), "")
        self.assertEqual(genre_clusters.genre_detail_label("unknown"), "")


class GenreClusterApiTests(unittest.TestCase):
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

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        connection.request(
            method,
            path,
            body,
            {"Content-Type": "application/json"} if body else {},
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        result = json.loads(raw) if raw else {}
        connection.close()
        return response.status, result

    def test_create_stores_cluster_id(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "웹소설 신작",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "romfant",
                "cluster_id": "webnovel",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(project["cluster_id"], "webnovel")
        self.assertEqual(project["purpose"], "web_novel")

        status, listing = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        found = next(item for item in listing if item["id"] == project["id"])
        self.assertEqual(found["cluster_id"], "webnovel")

    def test_create_infers_cluster_from_purpose(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "추리 장편", "purpose": "general_novel", "main_genre": "mystery"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(project["cluster_id"], "genre_literature")

        status, essay = self.request(
            "POST",
            "/api/projects",
            {"title": "산문", "purpose": "essay", "main_genre": "other"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(essay["cluster_id"], "general_literature")

    def test_settings_update_reinfers_cluster(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "바꿀 작품", "purpose": "web_novel", "main_genre": "fantasy"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(project["cluster_id"], "webnovel")
        status, updated = self.request(
            "POST",
            f"/api/projects/{project['id']}/settings",
            {"purpose": "essay"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["cluster_id"], "general_literature")

    def test_legacy_row_backfill_on_init(self) -> None:
        with app.database() as connection:
            connection.execute(
                "INSERT INTO project(title, purpose, main_genre, sub_genre, cluster_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("옛 웹소설", "web_novel", "romance", "modern", ""),
            )
            connection.execute(
                "INSERT INTO project(title, purpose, main_genre, sub_genre, cluster_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("옛 동화", "fairy_tale", "preschool", "", ""),
            )
        with app.database() as connection:
            app.ensure_project_cluster_column(connection)
        with app.database() as connection:
            rows = {
                str(row["title"]): str(row["cluster_id"])
                for row in connection.execute(
                    "SELECT title, cluster_id FROM project WHERE title IN (?, ?)",
                    ("옛 웹소설", "옛 동화"),
                )
            }
        self.assertEqual(rows["옛 웹소설"], "webnovel")
        self.assertEqual(rows["옛 동화"], "fairytale")

    def test_create_stores_valid_genre_detail_and_rejects_mismatch(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "사극 로맨스",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "modern",
                "genre_detail": "historical",
            },
        )
        self.assertEqual(status, 201, project)
        self.assertEqual(project["genre_detail"], "historical")
        self.assertEqual(project["genre_detail_label"], "사극")

        status, listing = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        found = next(item for item in listing if item["id"] == project["id"])
        self.assertEqual(found["genre_detail"], "historical")
        self.assertEqual(found["genre_detail_label"], "사극")

        status, mismatched = self.request(
            "POST",
            "/api/projects",
            {
                "title": "잘못된 세부장르",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "modern",
                "genre_detail": "alt_history",
            },
        )
        self.assertEqual(status, 201, mismatched)
        self.assertEqual(mismatched["genre_detail"], "")
        self.assertEqual(mismatched["genre_detail_label"], "")

        status, female = self.request(
            "POST",
            "/api/projects",
            {
                "title": "여성향",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "female",
                "genre_detail": "oriental_romfant",
            },
        )
        self.assertEqual(status, 201, female)
        self.assertEqual(female["genre_detail"], "")

    def test_settings_update_genre_detail_and_clears_on_genre_change(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "세부장르 변경",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "romfant",
                "genre_detail": "oriental_romfant",
            },
        )
        self.assertEqual(status, 201, project)
        status, updated = self.request(
            "POST",
            f"/api/projects/{project['id']}/settings",
            {"genre_detail": "alt_history"},
        )
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["genre_detail"], "")
        self.assertEqual(updated["genre_detail_label"], "")

        status, restored = self.request(
            "POST",
            f"/api/projects/{project['id']}/settings",
            {"genre_detail": "oriental_romfant"},
        )
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored["genre_detail"], "oriental_romfant")
        self.assertEqual(restored["genre_detail_label"], "동양로판")

        status, switched = self.request(
            "POST",
            f"/api/projects/{project['id']}/settings",
            {"main_genre": "fantasy", "sub_genre": "female"},
        )
        self.assertEqual(status, 200, switched)
        self.assertEqual(switched["genre_detail"], "")
        self.assertEqual(switched["genre_detail_label"], "")

    def test_dynamic_context_includes_genre_detail_label(self) -> None:
        with_detail = app.SuperToryHandler._tory_dynamic_context_system_prompt(
            main_genre_label="로맨스",
            sub_genre_label="현대로맨스",
            genre_detail_label="사극",
        )
        self.assertIn("메인장르: 로맨스 · 서브장르: 현대로맨스 · 세부장르: 사극", with_detail)
        self.assertIn("세부 장르 (project_genre_detail): 사극", with_detail)

        without = app.SuperToryHandler._tory_dynamic_context_system_prompt(
            main_genre_label="로맨스",
            sub_genre_label="현대로맨스",
        )
        self.assertIn("메인장르: 로맨스 · 서브장르: 현대로맨스.", without)
        self.assertNotIn("세부장르:", without)

    def test_assist_reads_genre_detail_from_project(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "프롬프트 주입",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "alt_history",
            },
        )
        self.assertEqual(status, 201, project)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "analyze",
                "dry_run": True,
                "project_id": project["id"],
                "project_title": "프롬프트 주입",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "main_genre_label": "판타지",
                "sub_genre_label": "남성향 판타지",
                "scene_content": "문이 열렸다. 바람이 들어왔다.",
            },
        )
        self.assertEqual(status, 200, result)
        system = str(result.get("system") or result.get("full_prompt") or "")
        self.assertIn("세부장르: 대체역사", system)


if __name__ == "__main__":
    unittest.main()
