"""Sentence-like proper-noun filtering and empty romanized fallback."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import app
from services.translation_preparation_service import (
    _parse_detected_proper_nouns,
    is_sentence_like_proper_noun,
    serialize_proper_noun,
)


class SentenceLikeHeuristicTests(unittest.TestCase):
    def test_keeps_short_names_and_noun_phrases(self) -> None:
        for term in ("도릭스", "운석 도릭스", "파가몬 제국", "할리마초", "구속줄"):
            self.assertFalse(is_sentence_like_proper_noun(term), term)

    def test_drops_clause_endings_and_mid_particles(self) -> None:
        self.assertTrue(
            is_sentence_like_proper_noun(
                "도릭스는 출처를 알 수 없는 가공된 운석이며"
            )
        )
        self.assertTrue(
            is_sentence_like_proper_noun("모나 제국 서남단에 할리마초 재배지가 있으며")
        )

    def test_detected_max_length_drops_long_noun_strings(self) -> None:
        self.assertTrue(
            is_sentence_like_proper_noun(
                "abcdefghijklmnop",
                max_chars=15,
            )
        )
        self.assertFalse(is_sentence_like_proper_noun("할리마초", max_chars=15))


class ParseDetectedProperNounsFilterTests(unittest.TestCase):
    def test_keeps_noun_and_drops_sentence(self) -> None:
        raw = json.dumps({
            "proper_nouns": [
                {
                    "source_term": "도릭스",
                    "term_type": "item",
                    "romanized": "Dorix",
                    "fit_judgment": "fits",
                    "judgment_reason": "짧은 이름",
                    "suggested_alternatives": [],
                },
                {
                    "source_term": "도릭스는 출처를 알 수 없는 가공된 운석이며",
                    "term_type": "item",
                    "romanized": "도릭스는 출처를 알 수 없는 가공된 운석이며",
                    "fit_judgment": "fits",
                    "judgment_reason": "문장",
                    "suggested_alternatives": [],
                },
            ]
        }, ensure_ascii=False)
        parsed = _parse_detected_proper_nouns(raw)
        self.assertEqual([item["source_term"] for item in parsed], ["도릭스"])


class StoredSentenceLikeCleanupTests(unittest.TestCase):
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
            "INSERT INTO project(title) VALUES ('문장형 고유명사 정리')"
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

    def test_list_drops_sentence_like_stored_rows(self) -> None:
        self.connection.execute(
            "INSERT INTO translation_proper_nouns("
            "translation_job_id, source_term, term_type, source, "
            "user_decision, final_term, created_at) "
            "VALUES (?, ?, 'item', 'character_index', 'keep_as_is', ?, datetime('now'))",
            (
                self.job_id,
                "모나 제국 서남단에 할리마초 재배지가 있으며",
                "모나 제국 서남단에 할리마초 재배지가 있으며",
            ),
        )
        self.connection.execute(
            "INSERT INTO translation_proper_nouns("
            "translation_job_id, source_term, term_type, source, created_at) "
            "VALUES (?, '도릭스', 'item', 'ai_detected', datetime('now'))",
            (self.job_id,),
        )
        self.connection.commit()
        payload = app.get_translation_preparation_service(
            self.connection
        ).list_proper_nouns(self.job_id)
        names = [item["source_term"] for item in payload["proper_nouns"]]
        self.assertEqual(names, ["도릭스"])

    def test_decide_keep_romanized_does_not_copy_korean_source(self) -> None:
        cursor = self.connection.execute(
            "INSERT INTO translation_proper_nouns("
            "translation_job_id, source_term, term_type, source, "
            "suggested_alternatives_json, created_at) "
            "VALUES (?, '도릭스', 'item', 'ai_detected', ?, datetime('now'))",
            (self.job_id, json.dumps({"romanized": "", "alternatives": []})),
        )
        noun_id = int(cursor.lastrowid)
        self.connection.commit()
        service = app.get_translation_preparation_service(self.connection)
        with self.assertRaises(ValueError) as raised:
            service.decide_proper_noun(
                noun_id, {"user_decision": "keep_romanized", "final_term": ""}
            )
        self.assertIn("로마자 표기가 없어요", str(raised.exception))
        row = service.repository.get_proper_noun(noun_id)
        serialized = serialize_proper_noun(row)
        self.assertTrue(serialized["needs_translation_term"])
        self.assertFalse(str(serialized.get("final_term") or "").strip())
