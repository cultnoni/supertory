"""Contract checks for translation segment persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
from repositories.translation_job_repository import TranslationJobRepository
from repositories.translation_segment_repository import (
    TranslationSegmentRepository,
)


class TranslationSegmentRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()
        project = self.connection.execute(
            "INSERT INTO project(title) VALUES ('세그먼트 저장소 검증')"
        )
        scenes = [
            {
                "title": "1화",
                "chapter_title": "1장",
                "content_md": "\n\n".join(
                    f"첫 장 문단 {number}" for number in range(1, 41)
                ),
            },
            {
                "title": "2화",
                "chapter_title": "2장",
                "content_md": "둘째 장 첫 문단.\n\n둘째 장 둘째 문단.",
            },
        ]
        jobs = TranslationJobRepository(
            self.connection,
            scene_loader=lambda _connection, _project_id: scenes,
            paragraph_splitter=app.split_source_paragraphs,
            separator_checker=app.is_translation_separator_paragraph,
            timestamp_provider=app.utc_timestamp_now,
        )
        job = jobs.create_job(
            int(project.lastrowid), "en", "moderate", 1, 2, False
        )
        self.job_id = int(job["id"])
        jobs.seed_segments_for_chapters(self.job_id, [1, 2])
        self.repository = TranslationSegmentRepository(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_pending_batch_is_ordered_and_does_not_cross_chapter(self) -> None:
        pending = self.repository.get_pending_segments(self.job_id, 36)
        self.assertEqual(len(pending), 36)
        self.assertEqual(
            {int(row["chapter_number"]) for row in pending},
            {1},
        )
        self.assertEqual(
            [int(row["segment_order"]) for row in pending],
            list(range(1, 37)),
        )

    def test_batch_save_resets_review_and_polish_fields(self) -> None:
        row = self.repository.get_pending_segments(self.job_id, 1)[0]
        self.connection.execute(
            "UPDATE translation_segments SET is_approved = 1, "
            "needs_manual_review = 1, polish_text = 'old', "
            "polish_proposal_text = 'proposal', polish_choice = 'apply' "
            "WHERE id = ?",
            (int(row["id"]),),
        )
        count = self.repository.save_translated_batch(self.job_id, [{
            "id": int(row["id"]),
            "translated_text": "Translated",
            "translation_notes": [{"note": "ok"}],
        }])
        updated = self.repository.get_segment(int(row["id"]))
        self.assertEqual(count, 1)
        self.assertEqual(updated["translated_text"], "Translated")
        self.assertEqual(int(updated["is_approved"]), 0)
        self.assertEqual(int(updated["needs_manual_review"]), 0)
        self.assertIsNone(updated["polish_proposal_text"])
        self.assertIsNone(updated["polish_text"])
        self.assertIsNone(updated["polish_choice"])

    def test_fallback_note_and_manual_review_contract(self) -> None:
        row = self.repository.get_pending_segments(self.job_id, 1)[0]
        self.repository.save_translated_segment(
            self.job_id,
            int(row["id"]),
            row["source_text"],
            [{"note": "fallback"}],
            needs_manual_review=True,
        )
        self.repository.mark_segment_needs_review(
            int(row["id"]), "수동 확인"
        )
        updated = self.repository.get_segment(int(row["id"]))
        self.assertEqual(int(updated["needs_manual_review"]), 1)
        self.assertIn("수동 확인", updated["translation_notes_json"])

    def test_approved_query_and_polish_apply_keep_edit_contract(self) -> None:
        rows = self.repository.get_segments_for_chapter(self.job_id, 2)
        results = [{
            "id": int(row["id"]),
            "translated_text": f"translated {index}",
            "translation_notes": [],
        } for index, row in enumerate(rows, start=1)]
        self.repository.save_translated_batch(self.job_id, results)
        for row in rows:
            self.repository.set_segment_approval(int(row["id"]), True)
        approved = self.repository.get_approved_segments_for_chapter(
            self.job_id, 2
        )
        self.assertEqual(len(approved), 2)
        self.repository.save_polish_suggestions(self.job_id, 2, [
            {"index": 1, "polished_text": "polished 1"},
            {"index": 2, "polished_text": "polished 2"},
        ])
        applied = self.repository.apply_polish_selection(
            int(rows[0]["id"]), True, "edited polish"
        )
        kept = self.repository.apply_polish_selection(
            int(rows[1]["id"]), False, None
        )
        self.assertEqual(applied["polish_text"], "edited polish")
        self.assertEqual(applied["polish_choice"], "apply")
        self.assertEqual(kept["polish_text"], "translated 2")
        self.assertEqual(kept["polish_choice"], "keep")
        self.repository.apply_all_chapter_polish(self.job_id, 2)
        final = self.repository.get_segments_for_chapter(self.job_id, 2)
        self.assertTrue(all(row["polish_choice"] == "apply" for row in final))
        self.assertEqual(final[0]["polish_text"], "polished 1")

    def test_rejects_missing_or_other_job_segment_ids(self) -> None:
        with self.assertRaises(LookupError):
            self.repository.save_translated_batch(self.job_id, [{
                "id": 999999,
                "translated_text": "missing",
                "translation_notes": [],
            }])
        other_project = self.connection.execute(
            "INSERT INTO project(title) VALUES ('다른 작업')"
        )
        self.connection.execute(
            "INSERT INTO translation_jobs("
            "local_project_id, target_language, culture_localization_level, "
            "start_chapter, end_chapter, translate_all_chapters"
            ") VALUES (?, 'en', 'moderate', 1, 1, 0)",
            (int(other_project.lastrowid),),
        )
        other_job_id = int(
            self.connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        )
        other_segment = self.connection.execute(
            "INSERT INTO translation_segments("
            "translation_job_id, chapter_number, segment_order, source_text"
            ") VALUES (?, 1, 1, '다른 문단')",
            (other_job_id,),
        )
        with self.assertRaises(LookupError):
            self.repository.save_translated_batch(self.job_id, [{
                "id": int(other_segment.lastrowid),
                "translated_text": "wrong job",
                "translation_notes": [],
            }])


if __name__ == "__main__":
    unittest.main()
