"""첨삭 카드 의견 대화: 프롬프트 채우기·20턴 한도·FakeClaude."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
import feedback_store
from feedback_pipeline.card_reply import (
    COMMENT_TURN_LIMIT,
    LIMIT_MESSAGE,
    CardCommentLimit,
    comment_limit_reached,
    fill_card_reply_prompt,
    format_history,
    neighbor_paragraph_text,
    reply_to_card_comment,
)
from feedback_pipeline.claude_client import ClaudeError, FakeClaude


class CardReplyTests(unittest.TestCase):
    def test_prompt_fill_and_neighbors(self) -> None:
        paragraphs = [
            {"i": 1, "text": "앞앞 문단입니다.", "type": "text"},
            {"i": 2, "text": "바로 앞 문단입니다.", "type": "text"},
            {"i": 3, "text": "대상 원문입니다.", "type": "text"},
            {"i": 4, "text": "바로 뒤 문단입니다.", "type": "text"},
            {"i": 5, "text": "뒤뒤 문단입니다.", "type": "text"},
        ]
        prompt = fill_card_reply_prompt(
            card={
                "title": "설명이 장면을 멈춰요",
                "reason": "대화 뒤에 해설이 붙습니다.",
                "kind": "structure",
                "style_type": "",
                "original_text": "대상 원문입니다.",
                "suggestion": None,
                "start_para": 3,
                "end_para": 3,
            },
            paragraphs=paragraphs,
            project_info="제목: 로판\n장르: 웹소설 / 로맨스 판타지",
            history=[
                {"role": "user", "body": "이전 질문"},
                {"role": "assistant", "body": "이전 답"},
            ],
            user_message="대화가 길어져서 일부러 설명을 넣은 건데 문제인가요?",
        )
        self.assertIn("설명이 장면을 멈춰요", prompt)
        self.assertIn("structure", prompt)
        self.assertIn("대상 원문입니다.", prompt)
        self.assertIn("(없음)", prompt)
        self.assertIn("바로 앞 문단입니다.", prompt)
        self.assertIn("바로 뒤 문단입니다.", prompt)
        self.assertIn("앞앞 문단입니다.", prompt)
        self.assertIn("로맨스 판타지", prompt)
        self.assertIn("사용자: 이전 질문", prompt)
        self.assertIn("토리: 이전 답", prompt)
        self.assertIn("대화가 길어져서 일부러 설명을 넣은 건데 문제인가요?", prompt)
        self.assertEqual(
            neighbor_paragraph_text(paragraphs, 3, 3).count("[P"),
            4,
        )

    def test_history_caps_at_ten(self) -> None:
        rows = [{"role": "user", "body": f"turn-{i:02d}"} for i in range(12)]
        text = format_history(rows, 10)
        self.assertNotIn("turn-00", text)
        self.assertNotIn("turn-01", text)
        self.assertIn("turn-02", text)
        self.assertIn("turn-11", text)

    def test_limit_helper(self) -> None:
        self.assertFalse(comment_limit_reached(19))
        self.assertTrue(comment_limit_reached(20))
        self.assertTrue(comment_limit_reached(21))

    def test_reply_saves_pair_and_refuses_over_limit(self) -> None:
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        original_dir = app.DATA_DIR
        original_db = app.DATABASE_PATH
        app.DATA_DIR = Path(temp.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        conn = app.connect()
        self.addCleanup(conn.close)
        self.addCleanup(lambda: setattr(app, "DATA_DIR", original_dir))
        self.addCleanup(lambda: setattr(app, "DATABASE_PATH", original_db))
        project_id = int(conn.execute("INSERT INTO project(title) VALUES ('로판')").lastrowid)
        chapter_id = int(
            conn.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (project_id,),
            ).lastrowid
        )
        scene_id = int(
            conn.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                "VALUES (?, ?, '2화', 0)",
                (project_id, chapter_id),
            ).lastrowid
        )
        run_id = feedback_store.create_run(
            conn, project_id, "analyze", "claude-sonnet-5", "v1", {}
        )
        feedback_store.add_run_scene(
            conn,
            run_id,
            project_id,
            scene_id,
            0,
            "2화",
            1,
            "hash",
            [{"i": 1, "text": "대상 원문입니다.", "type": "text"}],
        )
        card_id = feedback_store.add_cards(
            conn,
            run_id,
            [
                {
                    "scene_id": scene_id,
                    "kind": "structure",
                    "title": "구조",
                    "reason": "설명이 깁니다.",
                    "original_text": "대상 원문입니다.",
                    "start_para": 1,
                    "end_para": 1,
                }
            ],
        )[0]
        fake = FakeClaude(responses=["의도를 이해했어요. 무시하셔도 됩니다."])
        payload = reply_to_card_comment(
            conn,
            card_id,
            "대화가 길어져서 일부러 설명을 넣은 건데 문제인가요?",
            claude=fake,
        )
        self.assertEqual(len(payload["comments"]), 2)
        self.assertEqual(payload["comments"][0]["role"], "user")
        self.assertEqual(payload["comments"][1]["role"], "assistant")
        self.assertIn("의도를 이해했어요", payload["comments"][1]["body"])
        self.assertTrue(fake.prompts)
        self.assertNotIn("json_schema", fake.kwargs[0])
        self.assertEqual(fake.kwargs[0].get("max_tokens"), 512)
        self.assertEqual(fake.kwargs[0].get("thinking"), "off")

        for i in range(COMMENT_TURN_LIMIT - 2):
            role = "user" if i % 2 == 0 else "assistant"
            feedback_store.add_card_comment(conn, card_id, role, f"더미 {i}")
        blocked = FakeClaude(responses=["나오면 안 됨"])
        with self.assertRaises(CardCommentLimit) as raised:
            reply_to_card_comment(conn, card_id, "한 번 더", claude=blocked)
        self.assertEqual(str(raised.exception), LIMIT_MESSAGE)
        self.assertEqual(blocked.prompts, [])

    def test_failure_keeps_user_comment(self) -> None:
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        original_dir = app.DATA_DIR
        original_db = app.DATABASE_PATH
        app.DATA_DIR = Path(temp.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        conn = app.connect()
        self.addCleanup(conn.close)
        self.addCleanup(lambda: setattr(app, "DATA_DIR", original_dir))
        self.addCleanup(lambda: setattr(app, "DATABASE_PATH", original_db))
        project_id = int(conn.execute("INSERT INTO project(title) VALUES ('시험')").lastrowid)
        chapter_id = int(
            conn.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (project_id,),
            ).lastrowid
        )
        scene_id = int(
            conn.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                "VALUES (?, ?, '1화', 0)",
                (project_id, chapter_id),
            ).lastrowid
        )
        run_id = feedback_store.create_run(conn, project_id, "analyze", "m", "v1", {})
        feedback_store.add_run_scene(
            conn, run_id, project_id, scene_id, 0, "1화", 1, "h", [{"i": 1, "text": "원문", "type": "text"}]
        )
        card_id = feedback_store.add_cards(
            conn,
            run_id,
            [{"scene_id": scene_id, "kind": "style", "original_text": "원문", "reason": "이유"}],
        )[0]
        fake = FakeClaude(responses=[ClaudeError("끊김", code="network")])
        with self.assertRaises(ClaudeError):
            reply_to_card_comment(conn, card_id, "왜요?", claude=fake)
        rows = feedback_store.list_card_comments(conn, card_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["role"], "user")
        self.assertEqual(rows[0]["body"], "왜요?")


if __name__ == "__main__":
    unittest.main()
