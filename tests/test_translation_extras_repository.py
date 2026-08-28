"""Contract checks for translation extras persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
from repositories.translation_extras_repository import (
    TranslationExtrasRepository,
)
from repositories.translation_job_repository import TranslationJobRepository
from services.translation_extras_service import TranslationExtrasService


class TranslationExtrasRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()
        project = self.connection.execute(
            "INSERT INTO project(title) VALUES ('번역 부가기능 저장소')"
        )
        jobs = TranslationJobRepository(
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
        job = jobs.create_job(
            int(project.lastrowid), "en", "moderate", 1, 1, False
        )
        self.job_id = int(job["id"])
        jobs.seed_segments_for_chapters(self.job_id, [1])
        self.segment_id = int(
            self.connection.execute(
                "SELECT id FROM translation_segments "
                "WHERE translation_job_id = ?",
                (self.job_id,),
            ).fetchone()["id"]
        )
        self.repository = TranslationExtrasRepository(
            self.connection,
            timestamp_provider=app.utc_timestamp_now,
        )

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_word_lookup_and_context_cache_roundtrip(self) -> None:
        result = {
            "found": True,
            "status": "ok",
            "word": "rain",
            "meanings": [{"definitions": ["water from clouds"]}],
        }
        self.assertIsNone(
            self.repository.get_cached_word_lookup(self.segment_id, "rain")
        )
        self.repository.save_word_lookup_cache(
            self.segment_id, "rain", result
        )
        self.assertEqual(
            self.repository.get_cached_word_lookup(self.segment_id, "rain"),
            result,
        )
        updated = {**result, "phonetic": "/reɪn/"}
        self.repository.save_word_lookup_cache(
            self.segment_id, "rain", updated
        )
        self.assertEqual(
            self.repository.get_cached_word_lookup(
                self.segment_id, "rain"
            )["phonetic"],
            "/reɪn/",
        )
        self.repository.save_word_context_cache(
            self.segment_id, "rain", "비의 분위기를 살렸어요."
        )
        context = self.repository.get_cached_word_context(
            self.segment_id, "rain"
        )
        self.assertEqual(context["source"], "cache")
        self.assertIn("분위기", context["explanation"])

    def test_word_lookup_rejects_missing_segment(self) -> None:
        with self.assertRaises(LookupError):
            self.repository.save_word_lookup_cache(
                999999, "rain", {"found": False}
            )

    def test_dictionary_service_uses_segment_cache(self) -> None:
        calls = []

        def fetch(word: str, language: object):
            calls.append((word, language))
            return 200, [{
                "word": word,
                "meanings": [{
                    "partOfSpeech": "noun",
                    "definitions": [{"definition": "cached definition"}],
                }],
            }]

        service = TranslationExtrasService(
            self.repository,
            None,
            None,
            gemini_generate=lambda *_args, **_kwargs: "",
            dictionary_fetch=fetch,
        )
        first = service.lookup_word(self.segment_id, "rain", "en")
        second = service.lookup_word(self.segment_id, "rain", "en")
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["source"], "cache")
        self.assertEqual(calls, [("rain", "en")])

    def test_qa_message_and_history_contract(self) -> None:
        saved = self.repository.save_qa_message(
            self.job_id,
            "왜 rain인가요?",
            "비의 분위기를 살리기 위해서예요.",
            segment_id=self.segment_id,
            dragged_text="rain",
        )
        self.assertEqual(saved["user"]["role"], "user")
        self.assertEqual(saved["tori"]["role"], "tori")
        history = self.repository.get_qa_history(
            self.job_id, segment_id=self.segment_id
        )
        self.assertEqual(
            [row["role"] for row in history],
            ["user", "tori"],
        )
        self.assertEqual(len(self.repository.get_all_qa_history(self.job_id)), 2)

    def test_submission_package_upserts_and_preserves_range(self) -> None:
        created = self.repository.save_submission_package(
            self.job_id, "First logline", "First synopsis"
        )
        self.assertEqual(created["sample_chapters_range"], "1-1")
        updated = self.repository.save_submission_package(
            self.job_id, "Updated logline", "Updated synopsis"
        )
        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(
            self.repository.get_submission_package(
                self.job_id
            )["logline_translated"],
            "Updated logline",
        )


if __name__ == "__main__":
    unittest.main()
