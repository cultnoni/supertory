"""Contract checks for translation job persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
from repositories.translation_job_repository import TranslationJobRepository
from repositories.translation_segment_repository import (
    TranslationSegmentRepository,
)


class TranslationJobRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()
        cursor = self.connection.execute(
            "INSERT INTO project(title) VALUES ('번역 저장소 검증')"
        )
        self.project_id = int(cursor.lastrowid)
        scenes = [
            {
                "title": "1화",
                "chapter_title": "1장",
                "content_md": "첫 문단.\n\n둘째 문단.",
            },
            {
                "title": "2화",
                "chapter_title": "2장",
                "content_md": "셋째 문단.\n\n넷째 문단.",
            },
        ]
        self.repository = TranslationJobRepository(
            self.connection,
            scene_loader=lambda _connection, _project_id: scenes,
            paragraph_splitter=app.split_source_paragraphs,
            separator_checker=app.is_translation_separator_paragraph,
            timestamp_provider=app.utc_timestamp_now,
        )

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_create_get_update_and_segment_delete_contract(self) -> None:
        created = self.repository.create_job(
            self.project_id,
            "en",
            "moderate",
            1,
            2,
            False,
        )
        job_id = int(created["id"])
        self.assertEqual(
            int(self.repository.get_job(job_id)["local_project_id"]),
            self.project_id,
        )
        self.assertEqual(
            int(self.repository.get_job_for_project(self.project_id)["id"]),
            job_id,
        )

        updated = self.repository.update_job_settings(job_id, {
            "start_chapter": 1,
            "end_chapter": 1,
            "translate_all_chapters": 0,
            "cliffhanger_chapter": 1,
            "culture_localization_level": "as_is",
        })
        self.assertEqual(int(updated["end_chapter"]), 1)
        self.assertEqual(updated["culture_localization_level"], "as_is")
        self.assertEqual(
            self.repository.update_job_status(job_id, "in_progress")["status"],
            "in_progress",
        )

        self.assertEqual(
            self.repository.seed_segments_for_chapters(job_id, [1, 2]),
            4,
        )
        self.assertEqual(
            self.repository.delete_segments_outside_range(job_id, 1, 1),
            2,
        )
        self.assertEqual(
            TranslationSegmentRepository(
                self.connection
            ).translation_progress(job_id)["total_segments"],
            2,
        )

    def test_get_missing_job_returns_none(self) -> None:
        self.assertIsNone(self.repository.get_job(999999))
        self.assertIsNone(self.repository.get_job_for_project(self.project_id))
