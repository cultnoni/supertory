"""Index-term splitting and world-index proper-noun collection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
from world_import_analysis import compose_worldbuilding_md


class SplitIndexTermTokenTests(unittest.TestCase):
    def test_paren_list_emits_outer_name_and_inner_items(self) -> None:
        self.assertEqual(
            app._split_index_term_tokens("신비의 동물 (뮤온, 타우, 쿼크)"),
            ["신비의 동물", "뮤온", "타우", "쿼크"],
        )

    def test_outer_commas_keep_paren_group_then_split_inside(self) -> None:
        self.assertEqual(
            app._split_index_term_tokens(
                "〔토리〕 이능력, 신비의 동물 (뮤온, 타우, 쿼크), 운석 도릭스"
            ),
            ["이능력", "신비의 동물", "뮤온", "타우", "쿼크", "운석 도릭스"],
        )

    def test_slash_and_middot_split_outside_parens(self) -> None:
        self.assertEqual(
            app._split_index_term_tokens("알파 / 베타 · 감마"),
            ["알파", "베타", "감마"],
        )

    def test_inner_list_also_splits_slash(self) -> None:
        self.assertEqual(
            app._split_index_term_tokens("그룹 (알파/베타)"),
            ["그룹", "알파", "베타"],
        )

    def test_nested_parens_stay_one_token(self) -> None:
        self.assertEqual(
            app._split_index_term_tokens("바깥 (안쪽 (더안))"),
            ["바깥 (안쪽 (더안))"],
        )

    def test_strips_tori_prefix_from_display_token(self) -> None:
        self.assertEqual(
            app._split_index_term_tokens("〔토리〕 파가몬 제국"),
            ["파가몬 제국"],
        )


class ResolveIndexTermTypeTests(unittest.TestCase):
    def test_empire_place_vs_organization_defaults_to_organization(self) -> None:
        self.assertEqual(
            app._resolve_index_term_type("파가몬 제국", "place"),
            "organization",
        )
        self.assertEqual(
            app._resolve_index_term_type(
                "모나 제국", "organization", "place"
            ),
            "organization",
        )

    def test_plain_place_stays_place_when_not_org_hint(self) -> None:
        self.assertEqual(
            app._resolve_index_term_type("우산골", "place", "place"),
            "place",
        )


class CollectCharacterWorldIndexTermsTests(unittest.TestCase):
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

    def test_merges_tori_prefixed_duplicates_and_splits_paren_list(self) -> None:
        md = compose_worldbuilding_md({
            "locale": "〔토리〕 파가몬 제국, 모나 제국",
            "factions": "〔토리〕 모나 제국, 파가몬 제국, 검은 기사단",
            "special": "〔토리〕 이능력, 신비의 동물 (뮤온, 타우, 쿼크), 운석 도릭스",
        })
        with app.database() as connection:
            project_id = int(
                connection.execute(
                    "INSERT INTO project(title, worldbuilding_md) VALUES (?, ?)",
                    ("인덱스 용어 검증", md),
                ).lastrowid
            )
            terms = app.collect_character_world_index_terms(connection, project_id)

        names = [item["source_term"] for item in terms]
        self.assertEqual(names.count("파가몬 제국"), 1)
        self.assertEqual(names.count("모나 제국"), 1)
        self.assertTrue(all(not name.startswith("〔토리〕") for name in names))
        by_name = {item["source_term"]: item["term_type"] for item in terms}
        self.assertEqual(by_name["파가몬 제국"], "organization")
        self.assertEqual(by_name["모나 제국"], "organization")
        self.assertEqual(by_name["검은 기사단"], "organization")
        self.assertEqual(by_name["신비의 동물"], "item")
        self.assertEqual(by_name["뮤온"], "item")
        self.assertEqual(by_name["타우"], "item")
        self.assertEqual(by_name["쿼크"], "item")
        self.assertEqual(by_name["이능력"], "item")
        self.assertEqual(by_name["운석 도릭스"], "item")
        self.assertNotIn("신비의 동물 (뮤온", names)
        self.assertNotIn("쿼크)", names)


class CollapseStoredProperNounsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()
        from repositories.translation_job_repository import TranslationJobRepository

        project = self.connection.execute(
            "INSERT INTO project(title) VALUES ('저장된 고유명사 합치기')"
        )
        job_repository = TranslationJobRepository(
            self.connection,
            scene_loader=lambda _connection, _project_id: [{
                "title": "1화",
                "chapter_title": "1장",
                "content_md": "첫 문단.",
            }],
            paragraph_splitter=app.split_source_paragraphs,
            separator_checker=app.is_translation_separator_paragraph,
            timestamp_provider=app.utc_timestamp_now,
        )
        job = job_repository.create_job(
            int(project.lastrowid),
            "en",
            "moderate",
            1,
            1,
            False,
        )
        self.job_id = int(job["id"])

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_list_merges_tori_prefix_duplicates_into_one_organization(self) -> None:
        for source_term, term_type in (
            ("〔토리〕 파가몬 제국", "place"),
            ("파가몬 제국", "organization"),
            ("〔토리〕 모나 제국", "organization"),
            ("모나 제국", "place"),
        ):
            self.connection.execute(
                "INSERT INTO translation_proper_nouns("
                "translation_job_id, source_term, term_type, source, created_at"
                ") VALUES (?, ?, ?, 'character_index', datetime('now'))",
                (self.job_id, source_term, term_type),
            )
        self.connection.commit()
        payload = app.get_translation_preparation_service(
            self.connection
        ).list_proper_nouns(self.job_id)
        by_name = {
            item["source_term"]: item["term_type"]
            for item in payload["proper_nouns"]
        }
        self.assertEqual(by_name.get("파가몬 제국"), "organization")
        self.assertEqual(by_name.get("모나 제국"), "organization")
        self.assertEqual(
            [item["source_term"] for item in payload["proper_nouns"]].count(
                "파가몬 제국"
            ),
            1,
        )
        self.assertTrue(
            all(
                not str(item["source_term"]).startswith("〔토리〕")
                for item in payload["proper_nouns"]
            )
        )
