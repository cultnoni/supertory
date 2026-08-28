"""Contract checks for scene content persistence."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import app
from repositories.scene_content_repository import (
    ROW_VERSION_CONFLICT_MESSAGE,
    SceneContentRepository,
)
from services.scene_content_service import SceneContentService


class SceneContentRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()
        project_id = int(
            self.connection.execute(
                "INSERT INTO project(title) VALUES ('본문 저장소 검증')"
            ).lastrowid
        )
        chapter_id = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (project_id,),
            ).lastrowid
        )
        self.scene_id = int(
            self.connection.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                "VALUES (?, ?, '1화', 0)",
                (project_id, chapter_id),
            ).lastrowid
        )
        self.connection.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
            "VALUES (?, 1, '초고', 1, 1)",
            (self.scene_id,),
        )
        self.connection.commit()
        self.repository = SceneContentRepository(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_get_current_revision_includes_body_and_row_version(self) -> None:
        current = self.repository.get_current_revision(self.scene_id)
        self.assertIsNotNone(current)
        self.assertEqual(current["content_md"], "초고")
        self.assertEqual(int(current["revision_no"]), 1)
        self.assertEqual(int(current["row_version"]), 1)

    def test_save_new_revision_skips_when_content_unchanged(self) -> None:
        first = self.repository.save_new_revision(
            self.scene_id, "초고", 1, save_note="저장", word_count=1
        )
        self.assertEqual(int(first["revision_no"]), 1)
        rows = self.connection.execute(
            "SELECT revision_no FROM scene_revision WHERE scene_id = ? ORDER BY revision_no",
            (self.scene_id,),
        ).fetchall()
        self.assertEqual([int(row["revision_no"]) for row in rows], [1])

    def test_save_new_revision_appends_consecutive_numbers(self) -> None:
        second = self.repository.save_new_revision(
            self.scene_id, "두번째 원고", 1, save_note="저장", word_count=2
        )
        third = self.repository.save_new_revision(
            self.scene_id, "세번째 원고", 1, save_note="저장", word_count=2
        )
        self.assertEqual(int(second["revision_no"]), 2)
        self.assertEqual(int(third["revision_no"]), 3)
        self.assertEqual(third["content_md"], "세번째 원고")
        rows = self.connection.execute(
            "SELECT revision_no, content_md, is_current FROM scene_revision "
            "WHERE scene_id = ? ORDER BY revision_no",
            (self.scene_id,),
        ).fetchall()
        self.assertEqual(
            [(int(row["revision_no"]), row["content_md"], int(row["is_current"])) for row in rows],
            [(1, "초고", 0), (2, "두번째 원고", 0), (3, "세번째 원고", 1)],
        )

    def test_save_new_revision_rejects_stale_row_version(self) -> None:
        with self.assertRaises(ValueError) as raised:
            self.repository.save_new_revision(
                self.scene_id, "충돌 원고", 99, save_note="저장", word_count=2
            )
        self.assertEqual(str(raised.exception), ROW_VERSION_CONFLICT_MESSAGE)
        current = self.repository.get_current_revision(self.scene_id)
        self.assertEqual(current["content_md"], "초고")
        self.assertEqual(int(current["revision_no"]), 1)

    def test_old_revision_content_cannot_be_updated(self) -> None:
        self.repository.save_new_revision(
            self.scene_id, "새 원고", 1, save_note="저장", word_count=2
        )
        with self.assertRaises(sqlite3.IntegrityError) as raised:
            self.connection.execute(
                "UPDATE scene_revision SET content_md = '변조' "
                "WHERE scene_id = ? AND revision_no = 1",
                (self.scene_id,),
            )
        self.assertIn("immutable", str(raised.exception).lower())

    def test_skipped_revision_numbers_are_rejected(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError) as raised:
            self.connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, is_current) "
                "VALUES (?, 3, '건너뜀', 0)",
                (self.scene_id,),
            )
        self.assertIn("consecutive", str(raised.exception).lower())

    def test_update_scene_meta_bumps_row_version(self) -> None:
        updated = self.repository.update_scene_meta(
            self.scene_id,
            {
                "title": "1화 수정",
                "synopsis_md": "줄거리",
                "notes_md": "메모",
                "status": "draft",
                "goal_word_count": 10,
                "goal_metric": "words",
            },
        )
        self.assertEqual(updated["title"], "1화 수정")
        self.assertEqual(updated["status"], "draft")
        self.assertEqual(int(updated["row_version"]), 2)
        current = self.repository.get_current_revision(self.scene_id)
        self.assertEqual(current["content_md"], "초고")


class SceneContentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.service = SceneContentService(
            database=app.database,
            word_count=app.word_count,
            parse_reference_links=app.parse_reference_links,
            goal_metrics=app.GOAL_METRICS,
        )
        with app.database() as connection:
            project_id = int(
                connection.execute(
                    "INSERT INTO project(title) VALUES ('본문 서비스 검증')"
                ).lastrowid
            )
            chapter_id = int(
                connection.execute(
                    "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                    (project_id,),
                ).lastrowid
            )
            self.scene_id = int(
                connection.execute(
                    "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                    "VALUES (?, ?, '1화', 0)",
                    (project_id, chapter_id),
                ).lastrowid
            )
            connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
                "VALUES (?, 1, '초고', 1, 1)",
                (self.scene_id,),
            )

    def tearDown(self) -> None:
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _meta(self, **overrides) -> dict:
        payload = {
            "title": "1화",
            "status": "draft",
            "synopsis_md": "",
            "notes_md": "",
            "goal_word_count": 0,
            "goal_metric": "chars_with_space",
            "save_note": "저장",
        }
        payload.update(overrides)
        return payload

    def test_persist_scene_creates_revision_only_when_body_changes(self) -> None:
        first = self.service.persist_scene(
            self.scene_id, "초고", self._meta(), 1
        )
        self.assertEqual(int(first["revision_no"]), 1)
        self.assertEqual(int(first["row_version"]), 2)
        second = self.service.persist_scene(
            self.scene_id, "비가 내렸다.", self._meta(), first["row_version"]
        )
        self.assertEqual(int(second["revision_no"]), 2)
        self.assertEqual(int(second["row_version"]), 3)
        with app.database() as connection:
            rows = connection.execute(
                "SELECT revision_no, content_md, is_current FROM scene_revision "
                "WHERE scene_id = ? ORDER BY revision_no",
                (self.scene_id,),
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["content_md"], "초고")
        self.assertEqual(int(rows[0]["is_current"]), 0)
        self.assertEqual(rows[1]["content_md"], "비가 내렸다.")
        self.assertEqual(int(rows[1]["is_current"]), 1)

    def test_persist_scene_title_only_does_not_create_revision(self) -> None:
        first = self.service.persist_scene(
            self.scene_id, "초고", self._meta(status="draft", synopsis_md="줄거리"), 1
        )
        renamed = self.service.persist_scene(
            self.scene_id, None, {"title": "제목만 바꿈"}, first["row_version"]
        )
        self.assertTrue(renamed["ok"])
        self.assertEqual(int(renamed["revision_no"]), 1)
        self.assertGreater(int(renamed["row_version"]), int(first["row_version"]))
        with app.database() as connection:
            rows = connection.execute(
                "SELECT revision_no, content_md, is_current FROM scene_revision "
                "WHERE scene_id = ? ORDER BY revision_no",
                (self.scene_id,),
            ).fetchall()
            scene = connection.execute(
                "SELECT title, status, synopsis_md, row_version FROM scene WHERE id = ?",
                (self.scene_id,),
            ).fetchone()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content_md"], "초고")
        self.assertEqual(int(rows[0]["is_current"]), 1)
        self.assertEqual(scene["title"], "제목만 바꿈")
        self.assertEqual(scene["status"], "draft")
        self.assertEqual(scene["synopsis_md"], "줄거리")
        self.assertEqual(int(scene["row_version"]), int(renamed["row_version"]))
        typed = self.service.persist_scene(
            self.scene_id,
            "이어서 쓴 문장",
            self._meta(title="제목만 바꿈", status="draft", synopsis_md="줄거리"),
            renamed["row_version"],
        )
        self.assertEqual(int(typed["revision_no"]), 2)
        with app.database() as connection:
            current = connection.execute(
                "SELECT content_md FROM scene_revision "
                "WHERE scene_id = ? AND is_current = 1",
                (self.scene_id,),
            ).fetchone()
        self.assertEqual(current["content_md"], "이어서 쓴 문장")

    def test_persist_scene_explicit_empty_body_creates_revision(self) -> None:
        first = self.service.persist_scene(self.scene_id, "초고", self._meta(), 1)
        emptied = self.service.persist_scene(
            self.scene_id, "", self._meta(), first["row_version"]
        )
        self.assertEqual(int(emptied["revision_no"]), 2)
        with app.database() as connection:
            current = connection.execute(
                "SELECT content_md FROM scene_revision "
                "WHERE scene_id = ? AND is_current = 1",
                (self.scene_id,),
            ).fetchone()
        self.assertEqual(current["content_md"], "")

    def test_persist_scene_rejects_stale_row_version(self) -> None:
        with self.assertRaises(ValueError) as raised:
            self.service.persist_scene(
                self.scene_id, "다른 화면", self._meta(), 99
            )
        self.assertEqual(str(raised.exception), ROW_VERSION_CONFLICT_MESSAGE)
        with app.database() as connection:
            current = connection.execute(
                "SELECT revision_no, content_md FROM scene_revision "
                "WHERE scene_id = ? AND is_current = 1",
                (self.scene_id,),
            ).fetchone()
            version = connection.execute(
                "SELECT row_version FROM scene WHERE id = ?",
                (self.scene_id,),
            ).fetchone()
        self.assertEqual(current["content_md"], "초고")
        self.assertEqual(int(current["revision_no"]), 1)
        self.assertEqual(int(version["row_version"]), 1)

    def test_merge_mobile_draft_inserts_revision_and_bumps_version(self) -> None:
        result = self.service.merge_mobile_draft(self.scene_id, "폰에서 이어서 씀")
        self.assertTrue(result["ok"])
        with app.database() as connection:
            rows = connection.execute(
                "SELECT revision_no, content_md, is_current FROM scene_revision "
                "WHERE scene_id = ? ORDER BY revision_no",
                (self.scene_id,),
            ).fetchall()
            version = connection.execute(
                "SELECT row_version FROM scene WHERE id = ?",
                (self.scene_id,),
            ).fetchone()
        self.assertEqual(len(rows), 2)
        self.assertEqual(int(rows[1]["revision_no"]), 2)
        self.assertEqual(rows[1]["content_md"], "폰에서 이어서 씀")
        self.assertEqual(int(rows[1]["is_current"]), 1)
        self.assertEqual(int(version["row_version"]), 2)
