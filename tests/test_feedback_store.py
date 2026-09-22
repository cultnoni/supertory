"""첨삭 피드백 저장소와 094 마이그레이션 계약."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import app
import feedback_store
import project_snapshot


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()
        self.project_id = int(
            self.connection.execute(
                "INSERT INTO project(title) VALUES ('피드백 저장 검증')"
            ).lastrowid
        )
        chapter_id = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (self.project_id,),
            ).lastrowid
        )
        self.scene_id = int(
            self.connection.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                "VALUES (?, ?, '1화', 0)",
                (self.project_id, chapter_id),
            ).lastrowid
        )
        self.scene_b_id = int(
            self.connection.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                "VALUES (?, ?, '2화', 1)",
                (self.project_id, chapter_id),
            ).lastrowid
        )
        self.connection.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
            "VALUES (?, 1, '초고', 1, 1)",
            (self.scene_id,),
        )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _seed_run(
        self,
        *,
        scene_id: int | None = None,
        kind: str = "analyze",
        scene_title: str | None = None,
    ) -> int:
        scene_id = self.scene_id if scene_id is None else scene_id
        title = scene_title or ("2화" if int(scene_id) == int(self.scene_b_id) else "1화")
        run_id = feedback_store.create_run(
            self.connection,
            self.project_id,
            kind,
            "gemini-test",
            "v1",
            {"lens": "style"},
        )
        feedback_store.add_run_scene(
            self.connection,
            run_id,
            self.project_id,
            scene_id,
            0,
            title,
            1,
            "hash-a",
            ["문단 하나", "문단 둘"],
        )
        return run_id

    def test_migration_applies_once(self) -> None:
        sql = (Path(__file__).resolve().parents[1] / "db" / "094_feedback_run.sql").read_text(
            encoding="utf-8"
        )
        self.connection.executescript(sql)
        self.connection.executescript(sql)
        count = self.connection.execute(
            "SELECT COUNT(*) FROM schema_migration WHERE version = 94"
        ).fetchone()[0]
        self.assertEqual(int(count), 1)
        name = self.connection.execute(
            "SELECT name FROM schema_migration WHERE version = 94"
        ).fetchone()[0]
        self.assertEqual(name, "feedback_run")

    def test_095_title_column_and_default(self) -> None:
        cols = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(feedback_card)").fetchall()
        }
        self.assertIn("title", cols)
        run_id = self._seed_run()
        card_ids = feedback_store.add_cards(
            self.connection,
            run_id,
            [{"scene_id": self.scene_id, "kind": "style", "original_text": "원문"}],
        )
        title = self.connection.execute(
            "SELECT title FROM feedback_card WHERE id = ?",
            (card_ids[0],),
        ).fetchone()[0]
        self.assertEqual(title, "")
        named = feedback_store.add_cards(
            self.connection,
            run_id,
            [
                {
                    "scene_id": self.scene_id,
                    "kind": "structure",
                    "title": "같은 내용이 반복된 문단",
                    "original_text": "중복",
                }
            ],
        )
        stored = self.connection.execute(
            "SELECT title FROM feedback_card WHERE id = ?",
            (named[0],),
        ).fetchone()[0]
        self.assertEqual(stored, "같은 내용이 반복된 문단")
        count = self.connection.execute(
            "SELECT COUNT(*) FROM schema_migration WHERE version = 95"
        ).fetchone()[0]
        self.assertEqual(int(count), 1)

    def test_card_content_is_immutable_status_is_not(self) -> None:
        run_id = self._seed_run()
        card_ids = feedback_store.add_cards(
            self.connection,
            run_id,
            [
                {
                    "scene_id": self.scene_id,
                    "kind": "style",
                    "style_type": "pace",
                    "original_text": "원문",
                    "reason": "이유",
                    "edit_plan": "고침",
                    "suggestion": "수정안",
                }
            ],
        )
        card_id = card_ids[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE feedback_card SET original_text = '바꿈' WHERE id = ?",
                (card_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE feedback_card SET reason = '다른 이유' WHERE id = ?",
                (card_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE feedback_card SET title = '다른 제목' WHERE id = ?",
                (card_id,),
            )
        feedback_store.set_card_status(self.connection, card_id, "ignored")
        row = self.connection.execute(
            "SELECT status, original_text, status_changed_at FROM feedback_card WHERE id = ?",
            (card_id,),
        ).fetchone()
        self.assertEqual(row["status"], "ignored")
        self.assertEqual(row["original_text"], "원문")
        self.assertTrue(row["status_changed_at"])
        feedback_store.set_card_status(
            self.connection, card_id, "applied_edited", final_text="손본 문장"
        )
        edited = self.connection.execute(
            "SELECT status, final_text, applied_at FROM feedback_card WHERE id = ?",
            (card_id,),
        ).fetchone()
        self.assertEqual(edited["status"], "applied_edited")
        self.assertEqual(edited["final_text"], "손본 문장")
        self.assertTrue(edited["applied_at"])
        with self.assertRaises(ValueError):
            feedback_store.set_card_status(
                self.connection, card_id, "applied", final_text="막히면 안 됨"
            )

    def test_primary_unique_and_set_primary(self) -> None:
        first = self._seed_run()
        second = self._seed_run()
        feedback_store.set_primary(self.connection, first, self.scene_id)
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE feedback_run_scene SET is_primary = 1 "
                "WHERE run_id = ? AND scene_id = ?",
                (second, self.scene_id),
            )
        self.connection.rollback()
        feedback_store.set_primary(self.connection, second, self.scene_id)
        rows = self.connection.execute(
            "SELECT run_id, is_primary FROM feedback_run_scene WHERE scene_id = ? "
            "ORDER BY run_id",
            (self.scene_id,),
        ).fetchall()
        by_run = {int(row["run_id"]): int(row["is_primary"]) for row in rows}
        self.assertEqual(by_run[first], 0)
        self.assertEqual(by_run[second], 1)

    def test_list_runs_counts_status_and_primary(self) -> None:
        run_id = self._seed_run()
        card_ids = feedback_store.add_cards(
            self.connection,
            run_id,
            [
                {"scene_id": self.scene_id, "kind": "style", "original_text": "a"},
                {"scene_id": self.scene_id, "kind": "correction", "original_text": "b"},
                {"scene_id": self.scene_id, "kind": "consistency", "original_text": "c"},
                {"scene_id": self.scene_id, "kind": "structure", "original_text": "d"},
                {"scene_id": self.scene_id, "kind": "style", "original_text": "e"},
            ],
        )
        feedback_store.set_card_status(self.connection, card_ids[1], "applied")
        feedback_store.set_card_status(
            self.connection, card_ids[2], "applied_edited", final_text="고침"
        )
        feedback_store.set_card_status(self.connection, card_ids[3], "ignored")
        feedback_store.set_card_status(self.connection, card_ids[4], "alternate")
        feedback_store.set_primary(self.connection, run_id, self.scene_id)
        listed = feedback_store.list_runs(self.connection, self.project_id, self.scene_id)
        self.assertEqual(len(listed), 1)
        self.assertNotIn("report_md", listed[0])
        self.assertNotIn("report_json", listed[0])
        self.assertNotIn("raw_output", listed[0])
        self.assertEqual(listed[0]["is_primary"], 1)
        self.assertEqual(
            listed[0]["card_counts"],
            {
                "open": 1,
                "applied": 1,
                "applied_edited": 1,
                "ignored": 1,
                "alternate": 1,
            },
        )
        self.assertEqual(listed[0]["scene_id"], self.scene_id)
        self.assertEqual(listed[0]["scene_title"], "1화")

    def test_list_runs_without_scene_id_includes_scene_identity(self) -> None:
        first = self._seed_run()
        second = self._seed_run(scene_id=self.scene_b_id, scene_title="2화")
        listed = feedback_store.list_runs(self.connection, self.project_id)
        by_id = {int(row["id"]): row for row in listed}
        self.assertEqual(set(by_id), {first, second})
        self.assertEqual(by_id[first]["scene_id"], self.scene_id)
        self.assertEqual(by_id[first]["scene_title"], "1화")
        self.assertEqual(by_id[second]["scene_id"], self.scene_b_id)
        self.assertEqual(by_id[second]["scene_title"], "2화")
        only_b = feedback_store.list_runs(self.connection, self.project_id, self.scene_b_id)
        self.assertEqual([int(row["id"]) for row in only_b], [second])
        self.assertEqual(only_b[0]["scene_id"], self.scene_b_id)
        self.assertEqual(only_b[0]["scene_title"], "2화")

    def test_card_comments_list_and_snapshot(self) -> None:
        run_id = self._seed_run()
        card_ids = feedback_store.add_cards(
            self.connection,
            run_id,
            [{"scene_id": self.scene_id, "kind": "structure", "original_text": "원문", "reason": "이유"}],
        )
        card_id = card_ids[0]
        first = feedback_store.add_card_comment(
            self.connection, card_id, "user", "일부러 설명을 넣었어요"
        )
        second = feedback_store.add_card_comment(
            self.connection, card_id, "assistant", "의도를 이해했어요"
        )
        rows = feedback_store.list_card_comments(self.connection, card_id)
        self.assertEqual([int(row["id"]) for row in rows], [first, second])
        self.assertEqual(rows[0]["role"], "user")
        self.assertEqual(rows[1]["body"], "의도를 이해했어요")
        run = feedback_store.get_run(self.connection, run_id)
        self.assertEqual(run["cards"][0]["comment_count"], 2)
        self.connection.commit()
        created = project_snapshot.create_snapshot(
            app.DATABASE_PATH, app.DATA_DIR, self.project_id
        )
        feedback_store.delete_run(self.connection, run_id)
        self.connection.commit()
        self.assertEqual(
            feedback_store.list_card_comments(self.connection, card_id), []
        )
        self.connection.close()
        project_snapshot.restore_snapshot(
            app.DATABASE_PATH,
            app.DATA_DIR,
            self.project_id,
            created["filename"],
            create_safety=False,
        )
        restored = app.connect()
        try:
            listed = feedback_store.list_card_comments(restored, card_id)
            self.assertEqual(len(listed), 2)
            self.assertEqual(listed[0]["body"], "일부러 설명을 넣었어요")
        finally:
            restored.close()

    def test_delete_run_removes_cards_and_scenes(self) -> None:
        run_id = self._seed_run()
        feedback_store.add_cards(
            self.connection,
            run_id,
            [{"scene_id": self.scene_id, "kind": "style", "original_text": "원문"}],
        )
        feedback_store.delete_run(self.connection, run_id)
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM feedback_run WHERE id = ?", (run_id,)
            ).fetchone()
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM feedback_card WHERE run_id = ?", (run_id,)
            ).fetchone()
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM feedback_run_scene WHERE run_id = ?", (run_id,)
            ).fetchone()
        )

    def test_delete_scene_feedback_drops_empty_runs_keeps_others(self) -> None:
        only_here = self._seed_run()
        shared = self._seed_run()
        feedback_store.add_run_scene(
            self.connection,
            shared,
            self.project_id,
            self.scene_b_id,
            1,
            "2화",
            None,
            "",
            ["다른 회차"],
        )
        feedback_store.add_cards(
            self.connection,
            only_here,
            [{"scene_id": self.scene_id, "kind": "style", "original_text": "a"}],
        )
        feedback_store.add_cards(
            self.connection,
            shared,
            [
                {"scene_id": self.scene_id, "kind": "style", "original_text": "b"},
                {"scene_id": self.scene_b_id, "kind": "style", "original_text": "c"},
            ],
        )
        feedback_store.delete_scene_feedback(self.connection, self.scene_id)
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM feedback_run WHERE id = ?", (only_here,)
            ).fetchone()
        )
        leftover = feedback_store.get_run(self.connection, shared)
        self.assertIsNotNone(leftover)
        self.assertEqual([int(scene["scene_id"]) for scene in leftover["scenes"]], [self.scene_b_id])
        self.assertEqual(len(leftover["cards"]), 1)
        self.assertEqual(int(leftover["cards"][0]["scene_id"]), self.scene_b_id)
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM feedback_card WHERE scene_id = ?",
                (self.scene_id,),
            ).fetchone()
        )

    def test_import_legacy_entries_skips_duplicates_and_other_modes(self) -> None:
        entries = [
            {
                "id": "hist-1",
                "mode": "analyze",
                "modeLabel": "첨삭",
                "sceneId": self.scene_id,
                "sceneTitle": "1화",
                "text": "예전 리포트",
                "createdAt": "2026-01-02T03:04:05.000Z",
            },
            {
                "id": "hist-1",
                "mode": "analyze",
                "modeLabel": "첨삭",
                "sceneId": self.scene_id,
                "sceneTitle": "1화",
                "text": "중복",
                "createdAt": "2026-01-02T03:04:05.000Z",
            },
            {
                "id": "hist-proof",
                "mode": "proof",
                "modeLabel": "교정",
                "sceneId": self.scene_id,
                "sceneTitle": "1화",
                "text": "건너뜀",
                "createdAt": "2026-01-03T00:00:00.000Z",
            },
        ]
        first = feedback_store.import_legacy_entries(self.connection, self.project_id, entries)
        again = feedback_store.import_legacy_entries(self.connection, self.project_id, entries)
        self.assertEqual(len(first), 1)
        self.assertEqual(again, [])
        run = feedback_store.get_run(self.connection, first[0])
        self.assertEqual(run["run_kind"], "legacy")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["report_md"], "예전 리포트")
        self.assertEqual(run["cards"], [])
        self.assertEqual(run["params"]["legacy_id"], "hist-1")
        count = self.connection.execute(
            "SELECT COUNT(*) FROM feedback_run WHERE project_id = ? AND run_kind = 'legacy'",
            (self.project_id,),
        ).fetchone()[0]
        self.assertEqual(int(count), 1)

    def test_snapshot_restore_keeps_feedback_rows(self) -> None:
        run_id = self._seed_run()
        card_ids = feedback_store.add_cards(
            self.connection,
            run_id,
            [{"scene_id": self.scene_id, "kind": "style", "original_text": "스냅샷 원문", "title": "스냅샷 제목"}],
        )
        feedback_store.update_run(
            self.connection,
            run_id,
            status="ok",
            report_md="# 리포트",
            report_json={"summary": "ok"},
            finished_at="2026-09-20T00:00:00.000Z",
        )
        self.connection.commit()
        created = project_snapshot.create_snapshot(
            app.DATABASE_PATH, app.DATA_DIR, self.project_id
        )
        feedback_store.delete_run(self.connection, run_id)
        self.connection.commit()
        self.connection.close()
        project_snapshot.restore_snapshot(
            app.DATABASE_PATH,
            app.DATA_DIR,
            self.project_id,
            created["filename"],
            create_safety=False,
        )
        restored = app.connect()
        try:
            run = restored.execute(
                "SELECT status, report_md FROM feedback_run WHERE id = ?",
                (run_id,),
            ).fetchone()
            self.assertIsNotNone(run)
            self.assertEqual(run["status"], "ok")
            self.assertEqual(run["report_md"], "# 리포트")
            scene = restored.execute(
                "SELECT scene_id FROM feedback_run_scene WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            self.assertEqual(int(scene["scene_id"]), self.scene_id)
            card = restored.execute(
                "SELECT original_text, title FROM feedback_card WHERE id = ?",
                (card_ids[0],),
            ).fetchone()
            self.assertEqual(card["original_text"], "스냅샷 원문")
            self.assertEqual(card["title"], "스냅샷 제목")
        finally:
            restored.close()

    def test_hard_delete_scene_removes_feedback(self) -> None:
        run_id = self._seed_run()
        feedback_store.add_cards(
            self.connection,
            run_id,
            [{"scene_id": self.scene_id, "kind": "style", "original_text": "지워질 카드"}],
        )
        self.connection.execute(
            "UPDATE scene SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (self.scene_id,),
        )
        app.SuperToryHandler._hard_delete_scene(None, self.connection, self.scene_id)
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM feedback_card WHERE scene_id = ?",
                (self.scene_id,),
            ).fetchone()
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM feedback_run_scene WHERE scene_id = ?",
                (self.scene_id,),
            ).fetchone()
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM feedback_run WHERE id = ?",
                (run_id,),
            ).fetchone()
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM scene WHERE id = ?",
                (self.scene_id,),
            ).fetchone()
        )

    def test_get_run_includes_paragraphs_and_report(self) -> None:
        run_id = self._seed_run()
        feedback_store.update_run(
            self.connection,
            run_id,
            status="ok",
            report_md="본문",
            report_json={"ok": True},
            planned_cards=1,
        )
        feedback_store.add_cards(
            self.connection,
            run_id,
            [{"scene_id": self.scene_id, "kind": "style", "original_text": "원문"}],
        )
        run = feedback_store.get_run(self.connection, run_id)
        self.assertEqual(run["report_md"], "본문")
        self.assertEqual(run["report"], {"ok": True})
        self.assertEqual(run["scenes"][0]["paragraphs"], ["문단 하나", "문단 둘"])
        self.assertEqual(len(run["cards"]), 1)
        self.assertEqual(run["cards"][0]["kind"], "style")
