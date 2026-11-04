"""웹소설 장르 카드 거절 기반 「의도적으로 쓴 클리셰」 제안 (임시 DB)."""

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

DRAFT = "계약 연애를 의도적으로 쓰는 것은 의도. (이유: )"
WEBNOVEL = ("webnovel", "romance", "modern")


class WebnovelIntentSuggestTests(unittest.TestCase):
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

    def _project(self, cluster_id: str = "webnovel", main_genre: str = "romance", sub_genre: str = "modern") -> tuple[int, int]:
        pid = int(
            self.connection.execute(
                "INSERT INTO project(title, cluster_id, main_genre, sub_genre) "
                "VALUES ('웹소설-테스트', ?, ?, ?)",
                (cluster_id, main_genre, sub_genre),
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

    def _run(self, project_id: int, scene_id: int) -> int:
        run_id = feedback_store.create_run(
            self.connection, project_id, run_kind="analyze", model="test", prompt_version="test",
            params={}, pipeline="",
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
                    "original_text": f"계약 연애 설명이 깁니다 ({run_id})",
                    "reason": "계약 연애 설명이 깁니다",
                    "status": status,
                    "report_ref": ref if ref is not None else "GENRE:cliche_use",
                }
            ],
        )
        return ids[0]

    def _reject_in_two_runs(self, pid: int, sid: int, tag: str = "cliche_fatigue") -> tuple[int, int]:
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        self._card(run_a, sid, tag, status="ignored")
        self._card(run_a, sid, tag, status="ignored")
        self._card(run_b, sid, tag, status="ignored")
        return run_a, run_b

    def test_spec_opens_the_cliche_field_for_webnovel(self) -> None:
        spec = genre_intent_suggest.spec_for_cluster("webnovel")
        self.assertEqual(spec["section"], "cliche")
        self.assertEqual(spec["field"], "cliche_used")
        self.assertEqual(genre_intent_suggest.spec_for_cluster("genre_literature")["section"], "promise")
        self.assertEqual(genre_intent_suggest.spec_for_cluster("genre_literature")["field"], "promise_broken")

    def test_status_change_creates_a_notification_for_the_cliche_field(self) -> None:
        pid, sid = self._project()
        _run_a, run_b = self._reject_in_two_runs(pid, sid)
        last = self._card(run_b, sid, "cliche_fatigue", status="open")
        feedback_store.set_card_status(self.connection, last, "ignored")
        fake = FakeClaude([{"draft": DRAFT}])
        result = on_card_status_changed(self.connection, last, claude=fake)
        self.assertTrue(result and result.get("created"), result)
        notif = result["notification"]
        self.assertEqual(notif["kind"], "intent_suggest")
        self.assertEqual(
            notif["dedupe_key"],
            genre_intent_suggest.dedupe_key(pid, "cliche_fatigue", kind="webnovel"),
        )
        self.assertIn("클리셰 피로", notif["title"])
        self.assertIn("의도적으로 쓴 클리셰", notif["body"])
        self.assertNotIn("일부러 깬 약속", notif["body"])
        self.assertTrue(notif["payload"]["genre"])
        self.assertTrue(notif["payload"]["webnovel"])
        add = notif["actions"][0]
        self.assertEqual(add["label"], "추가")
        self.assertEqual(add["target"]["section"], "cliche")
        self.assertEqual(add["target"]["field"], "cliche_used")
        self.assertIn("(이유:", add["target"]["prefill"])
        self.assertEqual([a["label"] for a in notif["actions"]], ["추가", "아니요", "나중에"])

    def test_covered_cliche_blocks_the_notification(self) -> None:
        pid, sid = self._project()
        self.connection.execute(
            "UPDATE project SET cliche_used = ? WHERE id = ?", ("클리셰 피로는 의도", pid)
        )
        _a, run_b = self._reject_in_two_runs(pid, sid)
        last = self._card(run_b, sid, "cliche_fatigue", status="ignored")
        result = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "threshold_or_covered")

    def test_promise_field_does_not_cover_webnovel_cards(self) -> None:
        pid, sid = self._project()
        self.connection.execute(
            "UPDATE project SET promise_broken = ? WHERE id = ?", ("클리셰 피로는 의도", pid)
        )
        _a, run_b = self._reject_in_two_runs(pid, sid)
        last = self._card(run_b, sid, "cliche_fatigue", status="ignored")
        result = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
        self.assertTrue(result and result.get("created"), result)
        self.assertEqual(result["notification"]["actions"][0]["target"]["section"], "cliche")

    def test_genre_literature_still_opens_the_promise_field(self) -> None:
        pid, sid = self._project(cluster_id="genre_literature", main_genre="mystery", sub_genre="")
        run_a = self._run(pid, sid)
        run_b = self._run(pid, sid)
        self._card(run_a, sid, "clue_gap", status="ignored", ref="GENRE:clue_fairness")
        self._card(run_a, sid, "clue_gap", status="ignored", ref="GENRE:clue_fairness")
        last = self._card(run_b, sid, "clue_gap", status="ignored", ref="GENRE:clue_fairness")
        result = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
        self.assertTrue(result and result.get("created"), result)
        add = result["notification"]["actions"][0]
        self.assertEqual(add["target"]["section"], "promise")
        self.assertEqual(add["target"]["field"], "promise_broken")
        self.assertIn("일부러 깬 약속", result["notification"]["body"])

    def test_snooze_uses_the_same_genre_stats(self) -> None:
        pid, sid = self._project()
        _a, run_b = self._reject_in_two_runs(pid, sid)
        last = self._card(run_b, sid, "cliche_fatigue", status="ignored")
        first = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
        nid = first["notification"]["id"]
        record_snooze_baseline(self.connection, pid, nid)
        saved = self.connection.execute(
            "SELECT payload_json FROM tory_notification WHERE id = ?", (nid,)
        ).fetchone()
        self.assertIn('"snooze_baseline_count": 4', saved["payload_json"])
        tory_notifications.snooze_notification(self.connection, pid, nid, hours=24, action_id="later")
        waiting = on_card_status_changed(self.connection, last, claude=FakeClaude([{"draft": DRAFT}]))
        self.assertEqual(waiting.get("reason"), "snooze_waiting")

    def test_empty_cliche_draft_uses_webnovel_wording(self) -> None:
        empty = genre_intent_suggest.draft_promise_sentence(
            FakeClaude([{"draft": ""}]),
            tag="chemistry_weak",
            cards=[],
            prompt_file="genre/intent_cliche_draft_prompt.txt",
            empty_draft=genre_intent_suggest.spec_for_cluster("webnovel")["empty_draft"],
        )
        self.assertIn("케미 약함", empty["draft"])
        self.assertIn("의도적으로", empty["draft"])
        self.assertNotIn("일부러 그렇게", empty["draft"])
        self.assertIn("(이유:", empty["draft"])

    def test_below_threshold_is_skipped(self) -> None:
        pid, sid = self._project()
        run = self._run(pid, sid)
        card = self._card(run, sid, "cliche_fatigue", status="ignored")
        low = on_card_status_changed(self.connection, card, claude=FakeClaude([{"draft": DRAFT}]))
        self.assertTrue(low and low.get("skipped"))
        self.assertEqual(low.get("reason"), "threshold_or_covered")
        self.assertGreaterEqual(INTENT_SUGGEST_MIN_REJECTS, 2)
        self.assertGreaterEqual(INTENT_SUGGEST_MIN_RUNS, 2)


if __name__ == "__main__":
    unittest.main()
