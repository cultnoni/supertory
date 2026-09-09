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
        self.assertEqual(
            clusters["webnovel"]["sub_genres"],
            ["판타지", "무협", "역사·시대", "스포츠", "로맨스", "로맨스 판타지", "여성향 판타지"],
        )
        self.assertEqual(
            clusters["genre_literature"]["sub_genres"],
            ["SF", "미스테리/추리", "스릴러/호러", "정통판타지", "실험장르"],
        )
        self.assertEqual(clusters["general_literature"]["label"], "문학")
        self.assertEqual(
            clusters["fairytale"]["sub_genres"],
            ["영아", "유아", "초등 저학년"],
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
        self.assertEqual(mapped, ("web_novel", "romfant", ""))
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("webnovel", "female_fantasy"),
            ("web_novel", "female_fantasy", ""),
        )
        self.assertIsNone(genre_clusters.map_cluster_subgenre("webnovel", "light_novel"))
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("webnovel", "bl"),
            ("web_novel", "romance", "blgl"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("webnovel", "gl"),
            ("web_novel", "romance", "blgl"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("webnovel", "fantasy"),
            ("web_novel", "fantasy", ""),
        )
        self.assertIsNone(genre_clusters.map_cluster_subgenre("webnovel", "urban"))
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "experimental"),
            ("genre_literature", "experimental", ""),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "mystery_detective"),
            ("genre_literature", "mystery", "honkaku"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "detective"),
            ("genre_literature", "mystery", "honkaku"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "mystery"),
            ("genre_literature", "mystery", "honkaku"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "thriller"),
            ("genre_literature", "thriller", "psycho"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "sf"),
            ("genre_literature", "sf", "space"),
        )
        self.assertEqual(
            genre_clusters.map_cluster_subgenre("genre_literature", "traditional"),
            ("genre_literature", "traditional", ""),
        )
        self.assertIn("experimental", genre_clusters.GENRE_LITERATURE_MAIN)

    def test_genre_detail_allowed_values_and_labels(self) -> None:
        self.assertEqual(
            genre_clusters.normalize_genre_detail("romance", "modern", "historical"),
            "historical",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("romance", "modern", "period_west"),
            "period_west",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("romance", "romfant", "oriental_romfant"),
            "oriental_romfant",
        )
        self.assertEqual(genre_clusters.allowed_genre_details("romance", "bl"), frozenset({""}))
        self.assertEqual(genre_clusters.allowed_genre_details("romance", "gl"), frozenset({""}))
        self.assertEqual(genre_clusters.allowed_genre_details("romance", "blgl"), frozenset({""}))
        self.assertEqual(genre_clusters.normalize_genre_detail("romance", "bl", "historical"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("romance", "gl", "oriental_romfant"), "")
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "male", "alt_history"),
            "alt_history",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "male", "murim"),
            "murim",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "male", "murim_classic"),
            "murim_classic",
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
            genre_clusters.normalize_genre_detail("fantasy", "male", "isekai"),
            "isekai",
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
        self.assertEqual(
            genre_clusters.allowed_genre_details("fantasy", "female"),
            frozenset({"", "dimension", "modern", "period_east", "period_west"}),
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "female", "dimension"),
            "dimension",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "female", "modern"),
            "modern",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "female", "period_east"),
            "period_east",
        )
        self.assertEqual(
            genre_clusters.normalize_genre_detail("fantasy", "female", "period_west"),
            "period_west",
        )
        self.assertEqual(genre_clusters.normalize_genre_detail("romance", "modern", "sports"), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("romance", "modern", ""), "")
        self.assertEqual(genre_clusters.normalize_genre_detail("mystery", "honkaku", "historical"), "")
        self.assertEqual(genre_clusters.genre_detail_label("historical"), "사극")
        self.assertEqual(genre_clusters.genre_detail_label("period_west"), "시대 로맨스(서양)")
        self.assertEqual(genre_clusters.genre_detail_label("oriental_romfant"), "동양로판")
        self.assertEqual(genre_clusters.genre_detail_label("alt_history"), "대체역사")
        self.assertEqual(genre_clusters.genre_detail_label("murim"), "무협")
        self.assertEqual(genre_clusters.genre_detail_label("murim_classic"), "정통무협")
        self.assertEqual(genre_clusters.genre_detail_label("urban"), "현대판타지")
        self.assertEqual(genre_clusters.genre_detail_label("hidden_world"), "어반판타지")
        self.assertEqual(genre_clusters.genre_detail_label("traditional"), "정통판타지")
        self.assertEqual(genre_clusters.genre_detail_label("isekai"), "이세계판타지")
        self.assertEqual(genre_clusters.genre_detail_label("sports"), "스포츠물")
        self.assertEqual(genre_clusters.genre_detail_label(""), "")
        self.assertEqual(genre_clusters.genre_detail_label("unknown"), "")
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romfant", "period_east"),
            ("romance", "romfant", "oriental_romfant"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romance", "period_west"),
            ("romance", "modern", "period_west"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romance", "period_east"),
            ("romance", "modern", "historical"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("female_fantasy", "modern"),
            ("fantasy", "female", "modern"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("female_fantasy", "dimension"),
            ("fantasy", "female", "dimension"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("female_fantasy", "period_east"),
            ("fantasy", "female", "period_east"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("female_fantasy", "period_west"),
            ("fantasy", "female", "period_west"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("female_fantasy", ""),
            ("fantasy", "female", ""),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romfant", "bl"),
            ("romance", "romfant", "bl"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romfant", "gl"),
            ("romance", "romfant", "gl"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romance", "gl"),
            ("romance", "gl", ""),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romance", "bl"),
            ("romance", "bl", ""),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romance", "blgl"),
            ("romance", "bl", ""),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romfant", "blgl"),
            ("romance", "romfant", "bl"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romance", "other"),
            ("romance", "modern", ""),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romance", "custom:내가 만든 장르"),
            ("romance", "modern", ""),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romance", "custom:anything"),
            ("romance", "modern", ""),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romfant", "other"),
            ("romance", "romfant", ""),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("fantasy", "male", "murim"),
            ("fantasy", "male", "murim"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("fantasy", "male", "murim_classic"),
            ("fantasy", "male", "murim_classic"),
        )
        self.assertIn("murim_classic", genre_clusters.allowed_genre_details("fantasy", "male"))
        self.assertIn("murim", genre_clusters.allowed_genre_details("fantasy", "male"))
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("fantasy", "male", "alt_history"),
            ("fantasy", "male", "alt_history"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("historical"),
            ("fantasy", "male", "alt_history"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("historical", "", ""),
            ("fantasy", "male", "alt_history"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("romance", "modern", "historical"),
            ("romance", "modern", "historical"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("fantasy", "male", "sports"),
            ("fantasy", "male", "sports"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("sports"),
            ("fantasy", "male", "sports"),
        )
        self.assertEqual(
            genre_clusters.playbook_lookup_keys("sports", "", ""),
            ("fantasy", "male", "sports"),
        )
        self.assertIn("sports", genre_clusters.allowed_genre_details("fantasy", "male"))


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

    def test_settings_update_accepts_cluster_purpose_keys(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "종류 변경", "purpose": "web_novel", "main_genre": "romance", "sub_genre": "modern"},
        )
        self.assertEqual(status, 201)
        status, genre_lit = self.request(
            "POST",
            f"/api/projects/{project['id']}/settings",
            {
                "purpose": "genre_literature",
                "cluster_id": "genre_literature",
                "main_genre": "",
                "sub_genre": "",
            },
        )
        self.assertEqual(status, 200, genre_lit)
        self.assertEqual(genre_lit["purpose"], "genre_literature")
        self.assertEqual(genre_lit["cluster_id"], "genre_literature")

        status, literature = self.request(
            "POST",
            f"/api/projects/{project['id']}/settings",
            {
                "purpose": "literature",
                "cluster_id": "general_literature",
                "main_genre": "general_lit",
                "sub_genre": "",
            },
        )
        self.assertEqual(status, 200, literature)
        self.assertEqual(literature["purpose"], "literature")
        self.assertEqual(literature["cluster_id"], "general_literature")

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

    def test_create_fantasy_male_details_persist(self) -> None:
        for detail in ("traditional", "urban", "isekai", "hidden_world", "murim", "murim_classic"):
            status, project = self.request(
                "POST",
                "/api/projects",
                {
                    "title": f"판타지 {detail}",
                    "purpose": "web_novel",
                    "main_genre": "fantasy",
                    "sub_genre": "male",
                    "genre_detail": detail,
                    "cluster_id": "webnovel",
                },
            )
            self.assertEqual(status, 201, project)
            self.assertEqual(project["main_genre"], "fantasy")
            self.assertEqual(project["sub_genre"], "male")
            self.assertEqual(project["genre_detail"], detail)
            status, listing = self.request("GET", "/api/projects")
            self.assertEqual(status, 200)
            found = next(item for item in listing if item["id"] == project["id"])
            self.assertEqual(found["genre_detail"], detail)

    def test_webnovel_martial_picker_stores_fantasy_male_details(self) -> None:
        """화면 「무협」+정통/신무협 선택은 fantasy/male/{detail}로 저장된다."""
        for detail in ("murim_classic", "murim"):
            status, project = self.request(
                "POST",
                "/api/projects",
                {
                    "title": f"무협-{detail}",
                    "purpose": "web_novel",
                    "main_genre": "fantasy",
                    "sub_genre": "male",
                    "genre_detail": detail,
                    "cluster_id": "webnovel",
                },
            )
            self.assertEqual(status, 201, project)
            self.assertEqual(project["main_genre"], "fantasy")
            self.assertEqual(project["sub_genre"], "male")
            self.assertEqual(project["genre_detail"], detail)
            status, result = self.request(
                "POST",
                "/api/ai/assist",
                {
                    "dry_run": True,
                    "mode": "worldscan",
                    "project_id": project["id"],
                    "project_title": f"무협-{detail}",
                    "purpose": "web_novel",
                    "main_genre": "fantasy",
                    "sub_genre": "male",
                    "genre_detail": detail,
                    "scene_content": "강호에 소문이 퍼졌다.",
                },
            )
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn("[세부장르 추가 기준]", full, msg=detail)
            if detail == "murim_classic":
                self.assertIn("협의 관련 도덕적 갈등이 인물의 실제 선택", full)
                self.assertNotIn("정파의 이중잣대가 의도적 설정인지", full)
            else:
                self.assertIn("정파의 이중잣대가 의도적 설정인지", full)
                self.assertNotIn("협의 관련 도덕적 갈등이 인물의 실제 선택", full)

    def test_webnovel_historical_picker_stores_fantasy_male_alt_history(self) -> None:
        """화면 「역사·시대」 선택은 fantasy/male/alt_history로 저장된다."""
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "역사시대-대체역사",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "alt_history",
                "cluster_id": "webnovel",
            },
        )
        self.assertEqual(status, 201, project)
        self.assertEqual(project["main_genre"], "fantasy")
        self.assertEqual(project["sub_genre"], "male")
        self.assertEqual(project["genre_detail"], "alt_history")
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "dry_run": True,
                "mode": "worldscan",
                "project_id": project["id"],
                "project_title": "역사시대-대체역사",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "alt_history",
                "scene_content": "한양에 소문이 퍼졌다.",
            },
        )
        self.assertEqual(status, 200, result)
        full = str(result.get("full_prompt") or "")
        self.assertIn("[세부장르 추가 기준]", full)
        self.assertIn("의도된 개변 여부 구분", full)
        self.assertNotIn("시대적 위계·어투의 일관성", full)
        status, legacy = self.request(
            "POST",
            "/api/ai/assist",
            {
                "dry_run": True,
                "mode": "worldscan",
                "project_title": "레거시-historical",
                "purpose": "web_novel",
                "main_genre": "historical",
                "sub_genre": "",
                "genre_detail": "",
                "scene_content": "한양에 소문이 퍼졌다.",
            },
        )
        self.assertEqual(status, 200, legacy)
        legacy_full = str(legacy.get("full_prompt") or "")
        self.assertIn("[세부장르 추가 기준]", legacy_full)
        self.assertIn("의도된 개변 여부 구분", legacy_full)
        status, sageuk = self.request(
            "POST",
            "/api/ai/assist",
            {
                "dry_run": True,
                "mode": "worldscan",
                "project_title": "사극로맨스",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "modern",
                "genre_detail": "historical",
                "scene_content": "중전이 입을 다물었다.",
            },
        )
        self.assertEqual(status, 200, sageuk)
        sageuk_full = str(sageuk.get("full_prompt") or "")
        self.assertIn("[세부장르 추가 기준]", sageuk_full)
        self.assertIn("시대적 위계·어투의 일관성", sageuk_full)
        self.assertNotIn("의도된 개변 여부 구분", sageuk_full)

    def test_webnovel_sports_picker_stores_fantasy_male_sports(self) -> None:
        """화면 「스포츠」 선택은 fantasy/male/sports로 저장되고, fromDetail도 접히지 않는다."""
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "스포츠-학원축구",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "sports",
                "cluster_id": "webnovel",
            },
        )
        self.assertEqual(status, 201, project)
        self.assertEqual(project["main_genre"], "fantasy")
        self.assertEqual(project["sub_genre"], "male")
        self.assertEqual(project["genre_detail"], "sports")
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "dry_run": True,
                "mode": "worldscan",
                "project_id": project["id"],
                "project_title": "스포츠-학원축구",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "sports",
                "scene_content": "고교 리그 전반, 민호가 토트넘 유니폼을 입고 뛰었다.",
            },
        )
        self.assertEqual(status, 200, result)
        full = str(result.get("full_prompt") or "")
        self.assertIn("[세부장르 추가 기준]", full)
        self.assertIn("종목 규칙/전술 정확성", full)
        self.assertIn("의도된 각색인지 구분", full)
        self.assertNotIn("의도된 개변 여부 구분", full)
        self.assertNotIn("정파의 이중잣대가 의도적 설정인지", full)
        status, listing = self.request("GET", "/api/projects")
        self.assertEqual(status, 200, listing)
        found = next(item for item in listing if item["id"] == project["id"])
        self.assertEqual(found["main_genre"], "fantasy")
        self.assertEqual(found["sub_genre"], "male")
        self.assertEqual(found["genre_detail"], "sports")
        status, legacy = self.request(
            "POST",
            "/api/ai/assist",
            {
                "dry_run": True,
                "mode": "worldscan",
                "project_title": "레거시-sports",
                "purpose": "web_novel",
                "main_genre": "sports",
                "sub_genre": "",
                "genre_detail": "",
                "scene_content": "고교 리그 전반, 민호가 토트넘 유니폼을 입고 뛰었다.",
            },
        )
        self.assertEqual(status, 200, legacy)
        legacy_full = str(legacy.get("full_prompt") or "")
        self.assertIn("[세부장르 추가 기준]", legacy_full)
        self.assertIn("종목 규칙/전술 정확성", legacy_full)
        self.assertIn("의도된 각색인지 구분", legacy_full)
        status, alt = self.request(
            "POST",
            "/api/ai/assist",
            {
                "dry_run": True,
                "mode": "worldscan",
                "project_title": "대체역사-회귀",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "alt_history",
                "scene_content": "한양에 소문이 퍼졌다.",
            },
        )
        self.assertEqual(status, 200, alt)
        alt_full = str(alt.get("full_prompt") or "")
        self.assertIn("의도된 개변 여부 구분", alt_full)
        self.assertNotIn("종목 규칙/전술 정확성", alt_full)
        status, murim = self.request(
            "POST",
            "/api/ai/assist",
            {
                "dry_run": True,
                "mode": "worldscan",
                "project_title": "신무협-회귀",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "murim",
                "scene_content": "강호에 소문이 퍼졌다.",
            },
        )
        self.assertEqual(status, 200, murim)
        murim_full = str(murim.get("full_prompt") or "")
        self.assertIn("정파의 이중잣대가 의도적 설정인지", murim_full)
        self.assertNotIn("종목 규칙/전술 정확성", murim_full)

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

    def test_webnovel_stores_romance_and_romfant_bl_gl_separately(self) -> None:
        cases = (
            ("romance", "bl"),
            ("romance", "gl"),
            ("romfant", "bl"),
            ("romfant", "gl"),
        )
        for main, sub in cases:
            status, project = self.request(
                "POST",
                "/api/projects",
                {
                    "title": f"{main}-{sub}",
                    "purpose": "web_novel",
                    "main_genre": main,
                    "sub_genre": sub,
                },
            )
            self.assertEqual(status, 201, project)
            self.assertEqual(project["main_genre"], main)
            self.assertEqual(project["sub_genre"], sub)
            self.assertEqual(project.get("genre_detail") or "", "")

        status, legacy = self.request(
            "POST",
            "/api/projects",
            {
                "title": "legacy-blgl",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "blgl",
            },
        )
        self.assertEqual(status, 201, legacy)
        self.assertEqual(legacy["sub_genre"], "blgl")
        self.assertEqual(
            genre_clusters.playbook_lookup_keys(legacy["main_genre"], legacy["sub_genre"]),
            ("romance", "bl", ""),
        )

    def test_webnovel_stores_female_fantasy_details(self) -> None:
        for sub in ("dimension", "modern", "period_east", "period_west"):
            status, project = self.request(
                "POST",
                "/api/projects",
                {
                    "title": f"female-{sub}",
                    "purpose": "web_novel",
                    "main_genre": "female_fantasy",
                    "sub_genre": sub,
                },
            )
            self.assertEqual(status, 201, project)
            self.assertEqual(project["main_genre"], "female_fantasy")
            self.assertEqual(project["sub_genre"], sub)
            self.assertEqual(project.get("genre_detail") or "", "")
            self.assertEqual(
                genre_clusters.playbook_lookup_keys("female_fantasy", sub),
                ("fantasy", "female", sub),
            )

    def test_webnovel_stores_romance_other_and_custom_without_rewriting(self) -> None:
        for sub in ("other", "custom:내가 만든 장르"):
            title = f"romance-other-{sub[:8]}"
            status, project = self.request(
                "POST",
                "/api/projects",
                {
                    "title": title,
                    "purpose": "web_novel",
                    "main_genre": "romance",
                    "sub_genre": sub,
                },
            )
            self.assertEqual(status, 201, project)
            self.assertEqual(project["main_genre"], "romance")
            self.assertEqual(project["sub_genre"], sub)
            self.assertEqual(project.get("genre_detail") or "", "")
            status, result = self.request(
                "POST",
                "/api/ai/assist",
                {
                    "dry_run": True,
                    "mode": "worldscan",
                    "project_id": project["id"],
                    "project_title": title,
                    "purpose": "web_novel",
                    "main_genre": "romance",
                    "sub_genre": sub,
                    "scene_content": "문이 열렸다. 시선이 마주쳤다.",
                },
            )
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn("판타지 장치 오판 방지", full, msg=sub)

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
