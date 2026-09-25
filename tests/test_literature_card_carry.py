"""카드 이어받기·탐지 온도 단위 테스트."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
import feedback_store
from feedback_pipeline.claude_client import FakeClaude, is_sampling_locked_model
from feedback_pipeline.literature_cards import (
    DETECT_SELECT_TEMPERATURE,
    apply_card_carry,
    card_match_key,
    classify_previous_cards,
    find_previous_literature_run,
    generate_literature_cards,
    para_content_hash,
)
from feedback_pipeline.literature_text import assemble_manuscript


class CardCarryTests(unittest.TestCase):
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

    def _project_with_text(self, text: str = "<p>첫 문단이다.</p><p>둘째 문단이다.</p>") -> tuple[int, int, dict]:
        pid = int(
            self.connection.execute(
                "INSERT INTO project(title, cluster_id, main_genre, literary_form) "
                "VALUES ('이어받기', 'general_literature', 'general_lit', 'short')"
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
            "VALUES (?, 1, ?, 2, 1)",
            (sid, text),
        )
        rows = [
            {
                "id": sid,
                "title": "본문",
                "sort_order": 0,
                "chapter_sort": 0,
                "revision_no": 1,
                "content_md": text,
            }
        ]
        return pid, sid, assemble_manuscript(rows)

    def _prev_run(self, project_id: int, scene_id: int, cards: list[dict]) -> int:
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
        feedback_store.add_cards(self.connection, run_id, cards)
        return run_id

    def test_para_hash_and_classify(self) -> None:
        pid, sid, assembled = self._project_with_text()
        p1 = assembled["paragraphs"][0]["text"]
        self._prev_run(
            pid,
            sid,
            [
                {
                    "scene_id": sid,
                    "kind": "style",
                    "status": "open",
                    "start_para": 1,
                    "original_text": p1,
                    "start_quote": p1[:40],
                    "reason": "반복",
                    "card_form": "note",
                    "issue_tags_json": ["repeat"],
                },
                {
                    "scene_id": sid,
                    "kind": "style",
                    "status": "ignored",
                    "start_para": 2,
                    "original_text": assembled["paragraphs"][1]["text"],
                    "start_quote": "둘째",
                    "reason": "비문",
                    "card_form": "note",
                    "issue_tags_json": ["grammar"],
                },
            ],
        )
        prev_id = find_previous_literature_run(
            self.connection, pid, pipeline="literature_short", exclude_run_id=999
        )
        self.assertIsNotNone(prev_id)
        from feedback_pipeline.literature_cards import load_previous_run_cards

        classified = classify_previous_cards(assembled, load_previous_run_cards(self.connection, prev_id))
        self.assertEqual(len(classified["carry_open"]), 1)
        self.assertEqual(classified["carry_open"][0]["tags"], ["repeat"])
        self.assertTrue(any(card_match_key(assembled["paragraphs"][1]["text"], ["grammar"]) in classified["skip_keys"] for _ in [0]))
        self.assertTrue(classified["skip_keys"])

    def test_apply_carry_filters_and_injects(self) -> None:
        skip = {card_match_key("무시된 원문", ["repeat"])}
        merged = [
            {"quote": "무시된 원문", "original_text": "무시된 원문", "tags": ["repeat"], "reason": "x"},
            {"quote": "새 탐지", "original_text": "새 탐지", "tags": ["pov"], "reason": "y"},
        ]
        screened = list(merged)
        carry = [
            {
                "quote": "이어받기",
                "original_text": "이어받기 문단",
                "tags": ["cliche"],
                "reason": "z",
                "carried": True,
            }
        ]
        merged2, screened2, log = apply_card_carry(merged, screened, carry_open=carry, skip_keys=skip)
        self.assertEqual(len(merged2), 1)
        self.assertEqual(merged2[0]["tags"], ["pov"])
        self.assertEqual(len(screened2), 2)
        self.assertTrue(any(item["action"] == "carry_into_select" for item in log))
        self.assertTrue(any(item["detail"] == "이전 ignored/applied 유지" for item in log))

    def test_detect_select_temperature_when_unlocked(self) -> None:
        self.assertFalse(is_sampling_locked_model("claude-haiku-4-5-20251001"))
        self.assertTrue(is_sampling_locked_model("claude-sonnet-5"))
        self.assertEqual(DETECT_SELECT_TEMPERATURE, 0.0)

        pid, sid, assembled = self._project_with_text()
        # 탐지 1회 + 선별 1회. Haiku면 temperature 기록.
        fake = FakeClaude(
            [
                {"cards": []},
                {"keep": [], "drop": []},
            ]
        )
        generate_literature_cards(
            self.connection,
            project={
                "id": pid,
                "style_choice": "",
                "style_habit": "",
                "style_narration": "",
                "style_dialogue": "",
                "style_sentence": "",
                "style_lexicon": "",
                "intent_md": "",
            },
            assembled=assembled,
            report={"pipeline": "literature_short", "diagnoses": [], "tasks": [], "scene_map": {"scenes": []}},
            signals_summary="",
            options={
                "run_id": 0,
                "stage_models": {"cards": "claude-haiku-4-5-20251001", "verify": "claude-haiku-4-5-20251001"},
                "max_cost": 2,
            },
            claude=fake,
            params={"usage": {"stages": [], "cost_usd": 0.0}},
            max_cost=2,
        )
        temps = [kw.get("temperature") for kw in fake.kwargs]
        self.assertTrue(any(t == 0.0 for t in temps))

    def test_changed_paragraph_not_carried(self) -> None:
        pid, sid, assembled = self._project_with_text()
        self._prev_run(
            pid,
            sid,
            [
                {
                    "scene_id": sid,
                    "kind": "style",
                    "status": "open",
                    "start_para": 1,
                    "original_text": "예전에 있던 다른 문단",
                    "start_quote": "예전",
                    "reason": "반복",
                    "card_form": "note",
                    "issue_tags_json": ["repeat"],
                }
            ],
        )
        from feedback_pipeline.literature_cards import load_previous_run_cards

        prev_id = find_previous_literature_run(
            self.connection, pid, pipeline="literature_short", exclude_run_id=0
        )
        classified = classify_previous_cards(assembled, load_previous_run_cards(self.connection, prev_id))
        self.assertEqual(classified["carry_open"], [])


if __name__ == "__main__":
    unittest.main()
