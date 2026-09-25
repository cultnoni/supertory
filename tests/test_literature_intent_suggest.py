"""거절 기반 의도적 선택 제안 단위 테스트."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import app
import feedback_store
import tory_notifications
from feedback_pipeline.claude_client import FakeClaude
from feedback_pipeline.config import INTENT_SUGGEST_MIN_REJECTS, INTENT_SUGGEST_MIN_RUNS
from feedback_pipeline.literature_intent_suggest import (
    create_or_refresh_intent_suggest,
    intent_suggest_dedupe_key,
    on_card_status_changed,
    record_snooze_baseline,
    rejection_stats,
    should_propose,
    single_countable_tag,
    style_choice_covers_tag,
)


class IntentSuggestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _project(self) -> tuple[int, int]:
        pid = int(
            self.connection.execute(
                "INSERT INTO project(title, cluster_id, main_genre, sub_genre, literary_form) "
                "VALUES ('빈처-테스트', 'general_literature', 'general_lit', 'mid', 'short')"
            ).lastrowid
        )
        ch = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '본문', 0)",
                (pid,),
            ).lastrowid
        )
        sid = int(
            self.connection.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) VALUES (?, ?, '본문', 0)",
                (pid, ch),
            ).lastrowid
        )
        self.connection.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
            "VALUES (?, 1, '<p>본문</p>', 1, 1)",
            (sid,),
        )
        return pid, sid

    def _run(self, project_id: int, scene_id: int) -> int:
        run_id = feedback_store.create_run(
            self.connection,
            project_id,
            run_kind="analyze",
            model="test",
            prompt_version="test",
            params={},
            pipeline="literature_short",
        )
        feedback_store.add_run_scene(
            self.connection,
            run_id,
            project_id,
            scene_id,
            ord=0,
            scene_title="본문",
            revision_no=1,
            source_hash="h",
            paragraphs=[],
        )
        feedback_store.update_run(self.connection, run_id, status="ok")
        return run_id

    def _card(
        self,
        run_id: int,
        scene_id: int,
        tags: list[str],
        *,
        status: str = "open",
        original: str = "",
    ) -> int:
        ids = feedback_store.add_cards(
            self.connection,
            run_id,
            [
                {
                    "scene_id": scene_id,
                    "kind": "style",
                    "style_type": tags[0] if tags else "",
                    "original_text": original or f"비가 왔다. 비가 왔다. ({run_id})",
                    "reason": "반복",
                    "status": status,
                    "card_form": "note",
                    "issue_tags_json": tags,
                }
            ],
        )
        return ids[0]

    def test_single_tag_and_open_excluded(self) -> None:
        self.assertEqual(single_countable_tag(["repeat"]), "repeat")
        self.assertIsNone(single_countable_tag(["repeat", "excess"]))
        self.assertIsNone(single_countable_tag(["settings_conflict"]))
        self.assertFalse(style_choice_covers_tag("", "repeat"))
        self.assertTrue(style_choice_covers_tag("반복 표현은 리듬 의도", "repeat"))

    def test_count_ignores_open_and_applied_and_multi(self) -> None:
        pid, sid = self._project()
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        c1 = self._card(run_a, sid, ["repeat"], status="ignored")
        c2 = self._card(run_a, sid, ["repeat"], status="open")
        c3 = self._card(run_b, sid, ["repeat", "excess"], status="ignored")
        c4 = self._card(run_b, sid, ["repeat"], status="ignored")
        c5 = self._card(run_b, sid, ["repeat"], status="applied")
        stats = rejection_stats(self.connection, pid, "repeat")
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["run_count"], 2)
        self.assertNotIn(c2, [x["id"] for x in stats["cards"]])
        self.assertNotIn(c3, [x["id"] for x in stats["cards"]])
        self.assertNotIn(c5, [x["id"] for x in stats["cards"]])
        feedback_store.set_card_status(self.connection, c1, "applied")
        stats2 = rejection_stats(self.connection, pid, "repeat")
        self.assertEqual(stats2["count"], 1)
        self.assertEqual(stats2["run_count"], 1)
        self.assertFalse(should_propose(stats2))

    def test_threshold_needs_two_runs(self) -> None:
        pid, sid = self._project()
        run_a = self._run(pid, sid)
        for _ in range(INTENT_SUGGEST_MIN_REJECTS):
            self._card(run_a, sid, ["repeat"], status="ignored")
        stats = rejection_stats(self.connection, pid, "repeat")
        self.assertEqual(stats["count"], INTENT_SUGGEST_MIN_REJECTS)
        self.assertEqual(stats["run_count"], 1)
        self.assertFalse(should_propose(stats))
        run_b = self._run(pid, sid)
        self._card(run_b, sid, ["repeat"], status="ignored")
        stats2 = rejection_stats(self.connection, pid, "repeat")
        self.assertGreaterEqual(stats2["run_count"], INTENT_SUGGEST_MIN_RUNS)
        self.assertTrue(should_propose(stats2))

    def test_on_status_creates_notification_and_draft(self) -> None:
        pid, sid = self._project()
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        self._card(run_a, sid, ["repeat"], status="ignored")
        self._card(run_b, sid, ["repeat"], status="ignored")
        last = self._card(run_b, sid, ["repeat"], status="open")
        feedback_store.set_card_status(self.connection, last, "ignored")
        fake = FakeClaude(
            [{"draft": "화자가 같은 생각이나 감탄을 속으로 되풀이하는 것은 의도. (이유: )"}]
        )
        result = on_card_status_changed(self.connection, last, claude=fake)
        self.assertTrue(result and result.get("created"))
        self.assertIn("화자", result.get("draft") or "")
        self.assertIn("(이유:", result.get("draft") or "")
        self.assertNotIn("나레이터", result.get("draft") or "")
        notif = result["notification"]
        self.assertEqual(notif["kind"], "intent_suggest")
        self.assertEqual(notif["dedupe_key"], intent_suggest_dedupe_key(pid, "repeat"))
        self.assertIn("반복 표현", notif["title"])
        self.assertGreaterEqual(len(notif["payload"]["quotes"]), 2)
        labels = [a["label"] for a in notif["actions"]]
        self.assertEqual(labels, ["추가", "아니요", "나중에"])

    def test_dismiss_forever_blocks_recreation(self) -> None:
        pid, sid = self._project()
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        for run in (run_a, run_b):
            self._card(run, sid, ["repeat"], status="ignored")
        self._card(run_b, sid, ["repeat"], status="ignored")
        stats = rejection_stats(self.connection, pid, "repeat")
        first = create_or_refresh_intent_suggest(
            self.connection, pid, tag="repeat", stats=stats, draft="초안"
        )
        nid = first["notification"]["id"]
        tory_notifications.dismiss_notification(
            self.connection, pid, nid, forever=True, action_id="no"
        )
        again = create_or_refresh_intent_suggest(
            self.connection, pid, tag="repeat", stats=stats, draft="새초안"
        )
        self.assertTrue(again.get("blocked"))
        self.assertEqual(again.get("reason"), "dismissed_forever")

    def test_snooze_requires_additional_rejects(self) -> None:
        pid, sid = self._project()
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        cards = []
        for run in (run_a, run_b):
            cards.append(self._card(run, sid, ["repeat"], status="ignored"))
        cards.append(self._card(run_b, sid, ["repeat"], status="ignored"))
        stats = rejection_stats(self.connection, pid, "repeat")
        created = create_or_refresh_intent_suggest(
            self.connection, pid, tag="repeat", stats=stats, draft="초안"
        )
        nid = created["notification"]["id"]
        record_snooze_baseline(self.connection, pid, nid)
        tory_notifications.snooze_notification(self.connection, pid, nid, hours=24, action_id="later")
        waiting = create_or_refresh_intent_suggest(
            self.connection, pid, tag="repeat", stats=stats, draft="초안"
        )
        self.assertEqual(waiting.get("reason"), "snooze_waiting")
        # 새 거절 3개 추가
        run_c = self._run(pid, sid)
        for _ in range(INTENT_SUGGEST_MIN_REJECTS):
            self._card(run_c, sid, ["repeat"], status="ignored")
        stats2 = rejection_stats(self.connection, pid, "repeat")
        woken = create_or_refresh_intent_suggest(
            self.connection, pid, tag="repeat", stats=stats2, draft="다시"
        )
        self.assertEqual(woken.get("reason"), "snooze_reasked")
        self.assertEqual(woken["notification"]["status"], "unread")

    def test_draft_appends_reason_blank_and_renames_narrator(self) -> None:
        from feedback_pipeline.literature_intent_suggest import draft_intent_sentence

        fake = FakeClaude([{"draft": "나레이터의 독백 반복은 의도"}])
        out = draft_intent_sentence(
            fake,
            tag="repeat",
            cards=[{"original_text": "비가 왔다. 비가 왔다.", "reason": "반복"}],
        )
        self.assertIn("화자", out["draft"])
        self.assertNotIn("나레이터", out["draft"])
        self.assertIn("(이유:", out["draft"])

    def test_style_choice_blocks_propose(self) -> None:
        pid, sid = self._project()
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        for run in (run_a, run_b):
            self._card(run, sid, ["repeat"], status="ignored")
        self._card(run_b, sid, ["repeat"], status="ignored")
        self.connection.execute(
            "UPDATE project SET style_choice = ? WHERE id = ?",
            ("반복 표현은 의도적으로 남긴다", pid),
        )
        last = self._card(run_b, sid, ["repeat"], status="ignored")
        result = on_card_status_changed(
            self.connection, last, claude=FakeClaude([{"draft": "x"}])
        )
        self.assertTrue(result and result.get("skipped"))
        self.assertEqual(result.get("reason"), "threshold_or_covered")

        pid, sid = self._project()
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        for run in (run_a, run_b):
            self._card(run, sid, ["repeat"], status="ignored")
        self._card(run_b, sid, ["repeat"], status="ignored")
        self.connection.execute(
            "UPDATE project SET style_choice = ? WHERE id = ?",
            ("반복 표현은 의도적으로 남긴다", pid),
        )
        last = self._card(run_b, sid, ["repeat"], status="ignored")
        result = on_card_status_changed(
            self.connection, last, claude=FakeClaude([{"draft": "x"}])
        )
        self.assertTrue(result and result.get("skipped"))
        self.assertEqual(result.get("reason"), "threshold_or_covered")


if __name__ == "__main__":
    unittest.main()
