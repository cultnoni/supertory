"""장르 카드 거절 기반 「일부러 깬 약속」 제안 (임시 DB)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
import feedback_store
import tory_notifications
from feedback_pipeline import genre_intent_suggest
from feedback_pipeline.claude_client import FakeClaude
from feedback_pipeline.config import INTENT_SUGGEST_MIN_REJECTS, INTENT_SUGGEST_MIN_RUNS
from feedback_pipeline.literature_intent_suggest import (
    on_card_status_changed,
    record_snooze_baseline,
)

DRAFT = "서술자가 중요한 단서를 일부러 늦게 알려 주는 것은 의도. (이유: )"


class GenreIntentSuggestTests(unittest.TestCase):
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

    def _project(self, main_genre: str = "mystery") -> tuple[int, int]:
        pid = int(
            self.connection.execute(
                "INSERT INTO project(title, cluster_id, main_genre, sub_genre) "
                "VALUES ('추리-테스트', 'genre_literature', ?, '')",
                (main_genre,),
            ).lastrowid
        )
        ch = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '본문', 0)", (pid,)
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

    def _run(self, project_id: int, scene_id: int, pipeline: str = "") -> int:
        run_id = feedback_store.create_run(
            self.connection, project_id, run_kind="analyze", model="test", prompt_version="test",
            params={}, pipeline=pipeline,
        )
        feedback_store.add_run_scene(
            self.connection, run_id, project_id, scene_id, ord=0, scene_title="본문",
            revision_no=1, source_hash="h", paragraphs=[],
        )
        feedback_store.update_run(self.connection, run_id, status="ok")
        return run_id

    def _card(self, run_id: int, scene_id: int, tag: str, *, status: str = "open", ref: str | None = None) -> int:
        ids = feedback_store.add_cards(
            self.connection,
            run_id,
            [
                {
                    "scene_id": scene_id,
                    "kind": "structure",
                    "style_type": tag,
                    "original_text": f"단서가 늦게 나온다 ({run_id})",
                    "reason": "단서가 늦다",
                    "status": status,
                    "report_ref": ref if ref is not None else "GENRE:clue_fairness",
                }
            ],
        )
        return ids[0]

    def _reject_in_two_runs(self, pid: int, sid: int, tag: str = "clue_gap") -> tuple[int, int]:
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        self._card(run_a, sid, tag, status="ignored")
        self._card(run_a, sid, tag, status="ignored")
        self._card(run_b, sid, tag, status="ignored")
        return run_a, run_b

    def test_counts_only_ignored_genre_cards_of_the_tag(self) -> None:
        pid, sid = self._project()
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        self._card(run_a, sid, "clue_gap", status="ignored")
        self._card(run_a, sid, "clue_gap", status="open")
        self._card(run_b, sid, "clue_gap", status="applied")
        self._card(run_b, sid, "clue_gap", status="ignored")
        self._card(run_b, sid, "reveal_timing", status="ignored")
        # 웹소설 약점 카드(GENRE: 아님)는 세지 않는다
        self._card(run_b, sid, "clue_gap", status="ignored", ref="W1")
        stats = genre_intent_suggest.rejection_stats(self.connection, pid, "clue_gap")
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["run_count"], 2)

    def test_threshold_needs_enough_rejections_in_two_runs(self) -> None:
        pid, sid = self._project()
        run_a = self._run(pid, sid)
        for _ in range(INTENT_SUGGEST_MIN_REJECTS):
            self._card(run_a, sid, "clue_gap", status="ignored")
        stats = genre_intent_suggest.rejection_stats(self.connection, pid, "clue_gap")
        self.assertEqual(stats["count"], INTENT_SUGGEST_MIN_REJECTS)
        self.assertFalse(genre_intent_suggest.should_propose(stats))  # 실행이 한 번뿐
        run_b = self._run(pid, sid)
        self._card(run_b, sid, "clue_gap", status="ignored")
        stats = genre_intent_suggest.rejection_stats(self.connection, pid, "clue_gap")
        self.assertGreaterEqual(stats["run_count"], INTENT_SUGGEST_MIN_RUNS)
        self.assertTrue(genre_intent_suggest.should_propose(stats))

    def test_already_covered_by_the_broken_promise(self) -> None:
        self.assertTrue(genre_intent_suggest.promise_covers_tag("단서 누락·모호는 일부러 그렇게 쓴다", "clue_gap"))
        self.assertFalse(genre_intent_suggest.promise_covers_tag("", "clue_gap"))
        self.assertFalse(genre_intent_suggest.promise_covers_tag("열린 결말", "clue_gap"))

    def test_status_change_creates_a_notification_for_the_promise_field(self) -> None:
        pid, sid = self._project()
        _run_a, run_b = self._reject_in_two_runs(pid, sid)
        last = self._card(run_b, sid, "clue_gap", status="open")
        feedback_store.set_card_status(self.connection, last, "ignored")
        fake = FakeClaude([{"draft": DRAFT}])
        result = on_card_status_changed(self.connection, last, claude=fake)
        self.assertTrue(result and result.get("created"), result)
        notif = result["notification"]
        self.assertEqual(notif["kind"], "intent_suggest")
        self.assertEqual(notif["dedupe_key"], genre_intent_suggest.dedupe_key(pid, "clue_gap"))
        self.assertIn("단서 누락·모호", notif["title"])
        self.assertIn("일부러 깬 약속", notif["body"])
        self.assertTrue(notif["payload"]["genre"])
        add = notif["actions"][0]
        self.assertEqual(add["label"], "추가")
        self.assertEqual(add["target"]["section"], "promise")
        self.assertEqual(add["target"]["field"], "promise_broken")
        self.assertIn("(이유:", add["target"]["prefill"])
        self.assertEqual([a["label"] for a in notif["actions"]], ["추가", "아니요", "나중에"])

    def test_nothing_is_proposed_below_the_threshold_or_without_claude(self) -> None:
        pid, sid = self._project()
        run = self._run(pid, sid)
        card = self._card(run, sid, "clue_gap", status="ignored")
        low = on_card_status_changed(self.connection, card, claude=FakeClaude([{"draft": DRAFT}]))
        self.assertTrue(low and low.get("skipped"))
        self.assertEqual(low.get("reason"), "threshold_or_covered")
        _a, run_b = self._reject_in_two_runs(pid, sid)
        last = self._card(run_b, sid, "clue_gap", status="ignored")
        none = on_card_status_changed(self.connection, last, claude=None)
        self.assertEqual(none.get("reason"), "no_claude")

    def test_covered_promise_blocks_the_notification(self) -> None:
        pid, sid = self._project()
        self.connection.execute(
            "UPDATE project SET promise_broken = ? WHERE id = ?", ("단서 누락·모호는 의도", pid)
        )
        _a, run_b = self._reject_in_two_runs(pid, sid)
        last = self._card(run_b, sid, "clue_gap", status="ignored")
        result = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "threshold_or_covered")

    def test_no_forever_and_snooze_keep_the_literary_rules(self) -> None:
        pid, sid = self._project()
        _a, run_b = self._reject_in_two_runs(pid, sid)
        last = self._card(run_b, sid, "clue_gap", status="ignored")
        first = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
        nid = first["notification"]["id"]
        # 나중에: 지금 거절 수가 기준으로 저장된다(장르 통계로 센다)
        record_snooze_baseline(self.connection, pid, nid)
        saved = self.connection.execute(
            "SELECT payload_json FROM tory_notification WHERE id = ?", (nid,)
        ).fetchone()
        self.assertIn('"snooze_baseline_count": 4', saved["payload_json"])
        tory_notifications.snooze_notification(self.connection, pid, nid, hours=24, action_id="later")
        waiting = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
        self.assertEqual(waiting.get("reason"), "snooze_waiting")
        # 아니요: 같은 유형은 다시 묻지 않는다
        tory_notifications.dismiss_notification(self.connection, pid, nid, forever=True, action_id="no")
        for _ in range(INTENT_SUGGEST_MIN_REJECTS):
            self._card(self._run(pid, sid), sid, "clue_gap", status="ignored")
        blocked = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
        self.assertTrue(blocked.get("blocked"), blocked)

    def test_other_tags_use_their_own_notification(self) -> None:
        pid, sid = self._project()
        for tag in ("clue_gap", "reveal_timing"):
            _a, run_b = self._reject_in_two_runs(pid, sid, tag)
            last = self._card(run_b, sid, tag, status="ignored")
            result = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
            self.assertTrue(result.get("created"), result)
        keys = {
            row["dedupe_key"]
            for row in self.connection.execute("SELECT dedupe_key FROM tory_notification WHERE project_id = ?", (pid,))
        }
        self.assertEqual(
            keys,
            {genre_intent_suggest.dedupe_key(pid, "clue_gap"), genre_intent_suggest.dedupe_key(pid, "reveal_timing")},
        )

    def test_draft_always_ends_with_the_blank_reason(self) -> None:
        out = genre_intent_suggest.draft_promise_sentence(
            FakeClaude([{"draft": "세계 규칙에 예외를 두는 것은 의도"}]), tag="rule_break", cards=[]
        )
        self.assertIn("(이유:", out["draft"])
        empty = genre_intent_suggest.draft_promise_sentence(FakeClaude([{"draft": ""}]), tag="arc_leap", cards=[])
        self.assertIn("관계 전개 비약", empty["draft"])
        self.assertIn("(이유:", empty["draft"])

    def test_labels_cover_every_layer(self) -> None:
        self.assertEqual(genre_intent_suggest.tag_label("clue_gap"), "단서 누락·모호")
        self.assertEqual(genre_intent_suggest.tag_label("rule_break"), "규칙 위반")
        self.assertEqual(genre_intent_suggest.tag_label("emotion_telling"), "감정 설명 과잉")
        self.assertEqual(genre_intent_suggest.tag_label("unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()
