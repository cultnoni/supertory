"""Contract checks for translation preparation persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
from repositories.translation_job_repository import TranslationJobRepository
from repositories.translation_preparation_repository import (
    TranslationPreparationRepository,
)


class TranslationPreparationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()
        project = self.connection.execute(
            "INSERT INTO project(title) VALUES ('번역 준비 저장소 검증')"
        )
        job_repository = TranslationJobRepository(
            self.connection,
            scene_loader=lambda _connection, _project_id: [{
                "title": "1화",
                "chapter_title": "1장",
                "content_md": "첫 문단.\n\n둘째 문단.",
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
        job_repository.seed_segments_for_chapters(self.job_id, [1])
        self.repository = TranslationPreparationRepository(
            self.connection,
            timestamp_provider=app.utc_timestamp_now,
        )

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_formatting_and_scene_context_contract(self) -> None:
        rules = {
            "detected_conventions": [{"marker": "—", "meaning": "텔레파시"}],
            "recommended_handling": "preserve_with_note",
        }
        self.repository.save_formatting_rules(self.job_id, rules)
        self.assertEqual(
            self.repository.get_formatting_rules(self.job_id),
            rules,
        )

        self.repository.save_scene_contexts(self.job_id, [{
            "chapter_number": 1,
            "scene_order": 1,
            "start_paragraph_index": 0,
            "end_paragraph_index": 1,
            "relationship_tag": "초면-설렘",
            "mood_tag": "설렘",
            "situation_note": "비 오는 첫 만남",
        }])
        scenes = self.repository.get_scene_contexts(self.job_id)
        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0]["relationship_tag"], "초면-설렘")
        attached = self.connection.execute(
            "SELECT COUNT(*) AS n FROM translation_segments "
            "WHERE translation_job_id = ? AND scene_context_id = ?",
            (self.job_id, int(scenes[0]["id"])),
        ).fetchone()
        self.assertEqual(int(attached["n"]), 2)

    def test_proper_noun_save_update_and_confirm_contract(self) -> None:
        self.repository.save_proper_nouns(self.job_id, [
            {
                "source_term": "이오나",
                "term_type": "character",
                "fit_judgment": "fits",
                "romanized": "",
                "suggested_alternatives": [],
                "user_decision": "keep_as_is",
                "final_term": "이오나",
                "source": "character_index",
            },
            {
                "source_term": "우산골",
                "term_type": "place",
                "fit_judgment": "does_not_fit",
                "romanized": "Usangol",
                "suggested_alternatives": ["Rainvale"],
                "user_decision": None,
                "final_term": None,
                "source": "ai_detected",
            },
        ])
        nouns = self.repository.get_proper_nouns(self.job_id)
        self.assertEqual(len(nouns), 2)
        detected = next(row for row in nouns if row["source_term"] == "우산골")

        updated = self.repository.update_proper_noun(
            self.job_id,
            int(detected["id"]),
            "Rainvale",
            "ai_detected",
            user_decision="rename",
        )
        self.assertEqual(updated["final_term"], "Rainvale")
        self.assertEqual(updated["user_decision"], "rename")

        applied = self.repository.apply_proper_noun_ai_fields(
            self.job_id,
            int(nouns[0]["id"]),
            fit_judgment="fits",
            judgment_reason="'Iona' 표기가 자연스럽습니다.",
            romanized="Iona",
            suggested_alternatives=["Yona"],
        )
        self.assertEqual(applied["fit_judgment"], "fits")
        self.assertEqual(applied["judgment_reason"], "'Iona' 표기가 자연스럽습니다.")
        self.assertIsNone(applied["user_decision"])
        self.assertIsNone(applied["final_term"])
        self.assertIn("Iona", str(applied.get("suggested_alternatives_json") or ""))

        self.repository.confirm_all_proper_nouns(self.job_id)
        job = self.connection.execute(
            "SELECT proper_nouns_confirmed FROM translation_jobs WHERE id = ?",
            (self.job_id,),
        ).fetchone()
        self.assertEqual(int(job["proper_nouns_confirmed"]), 1)

    def test_delete_proper_noun_removes_row(self) -> None:
        self.repository.save_proper_nouns(self.job_id, [
            {
                "source_term": "우산골",
                "term_type": "place",
                "fit_judgment": "does_not_fit",
                "user_decision": "rename",
                "final_term": "Rainvale",
                "source": "ai_detected",
            },
        ])
        noun_id = int(self.repository.get_proper_nouns(self.job_id)[0]["id"])
        self.repository.delete_proper_noun(self.job_id, noun_id)
        self.assertEqual(self.repository.get_proper_nouns(self.job_id), [])
        with self.assertRaises(LookupError):
            self.repository.delete_proper_noun(self.job_id, noun_id)

    def test_save_user_added_and_suppress_keys(self) -> None:
        self.repository.save_proper_nouns(self.job_id, [{
            "source_term": "뮤온",
            "term_type": "item",
            "user_decision": "rename",
            "final_term": "Muon",
            "source": "user_added",
        }])
        saved = self.repository.get_proper_nouns(self.job_id)[0]
        self.assertEqual(saved["source"], "user_added")
        self.repository.suppress_proper_noun_term(self.job_id, "우산골")
        self.repository.suppress_proper_noun_term(self.job_id, "우산골")
        self.assertEqual(
            self.repository.suppressed_proper_noun_keys(self.job_id),
            {"우산골"},
        )
        self.repository.unsuppress_proper_noun_term(self.job_id, "우산골")
        self.assertEqual(
            self.repository.suppressed_proper_noun_keys(self.job_id),
            set(),
        )
        self.repository.confirm_all_proper_nouns(self.job_id)
        self.repository.clear_proper_nouns_confirmed(self.job_id)
        job = self.connection.execute(
            "SELECT proper_nouns_confirmed FROM translation_jobs WHERE id = ?",
            (self.job_id,),
        ).fetchone()
        self.assertEqual(int(job["proper_nouns_confirmed"]), 0)

    def test_save_proper_nouns_deduplicates_source_terms(self) -> None:
        noun = {
            "source_term": "세리나",
            "term_type": "character",
            "source": "character_index",
        }
        self.repository.save_proper_nouns(self.job_id, [noun, noun])
        self.repository.save_proper_nouns(self.job_id, [noun])
        self.assertEqual(len(self.repository.get_proper_nouns(self.job_id)), 1)

    def test_missing_records_follow_contract(self) -> None:
        self.assertIsNone(self.repository.get_proper_noun(999999))
        with self.assertRaises(LookupError):
            self.repository.get_formatting_rules(999999)
        with self.assertRaises(LookupError):
            self.repository.update_proper_noun(
                self.job_id,
                999999,
                "없음",
                "ai_detected",
            )
