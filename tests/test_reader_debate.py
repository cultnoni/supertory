"""Virtual-reader debate panel: session reuse, sequential persona turns, history."""

from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client

ROOT = Path(__file__).resolve().parents[1]
REAL_DB = ROOT / "data" / "supertory.sqlite3"

DEBATE_PERSONAS = [
    "roppan_cider",
    "roppan_narrative",
    "plausibility_absolutist",
]


class ReaderDebateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        self.original_generate = gemini_client.generate_text
        self.original_gap = app.READER_DEBATE_GEMINI_GAP_SECONDS
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.READER_DEBATE_GEMINI_GAP_SECONDS = 0
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.calls: list[dict] = []

        def _fake_generate(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system or ""})
            system_text = system or ""
            if "로맨스·로판 사이다파" in system_text:
                return "사이다가 아직 덜 터졌어. 억울함이 더 쌓여야 해."
            if "로맨스·로판 서사파" in system_text:
                return "사이다파 말도 일리 있지만, 선택의 동기만 납득되면 난 괜찮아."
            if "개연성·복선 설계 분석가" in system_text:
                return "앞선 말들과 별개로, 열린 떡밥이 회수되는지부터 봐야 해."
            return f"기본응답-{len(self.calls)}"

        gemini_client.generate_text = _fake_generate  # type: ignore[method-assign]

    def tearDown(self) -> None:
        gemini_client.generate_text = self.original_generate  # type: ignore[method-assign]
        app.READER_DEBATE_GEMINI_GAP_SECONDS = self.original_gap
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, object]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=60
        )
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = raw
        return response.status, data

    def _make_project(self) -> int:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "토론 테스트", "main_genre": "romfant", "sub_genre": "high"},
        )
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def test_migration_052_tables_exist(self) -> None:
        with app.database() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("reader_debate_sessions", tables)
            self.assertIn("reader_debate_messages", tables)
            version = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 52"
            ).fetchone()[0]
            self.assertEqual(version, "reader_debate")

    def test_persona_ids_key_is_sorted(self) -> None:
        self.assertEqual(
            app.reader_debate_persona_ids_key(
                ["plausibility_absolutist", "roppan_cider", "roppan_narrative"]
            ),
            "plausibility_absolutist,roppan_cider,roppan_narrative",
        )

    def test_post_debate_runs_personas_in_order_and_shares_context(self) -> None:
        pid = self._make_project()
        status, result = self.request(
            "POST",
            "/api/reader-debate",
            {
                "work_id": str(pid),
                "persona_ids": DEBATE_PERSONAS,
                "user_message": "이번 화 어떻게 봤어요?",
            },
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result.get("round_number"), 1)
        replies = result.get("replies") or []
        self.assertEqual(len(replies), 3)
        self.assertEqual(
            [item["persona_id"] for item in replies],
            DEBATE_PERSONAS,
        )
        self.assertTrue(all(item.get("ok") for item in replies))
        self.assertEqual(len(self.calls), 3)
        for call in self.calls:
            system = call["system"]
            self.assertIn(app.READER_DEBATE_TASK_ADDON, system)
            self.assertIn("[작품 정보]", system)
            self.assertNotIn("당신은 '토리'입니다", system)
        # 뒷사람이 앞사람 발언을 컨텍스트로 받는지
        second_prompt = self.calls[1]["prompt"]
        self.assertIn("사이다가 아직 덜 터졌어", second_prompt)
        third_prompt = self.calls[2]["prompt"]
        self.assertIn("사이다가 아직 덜 터졌어", third_prompt)
        self.assertIn("선택의 동기만 납득되면", third_prompt)

    def test_session_reused_and_history_grouped_by_round(self) -> None:
        pid = self._make_project()
        status, first = self.request(
            "POST",
            "/api/reader-debate",
            {
                "work_id": str(pid),
                "persona_ids": DEBATE_PERSONAS,
                "user_message": "첫 질문",
            },
        )
        self.assertEqual(status, 200, first)
        # 같은 조합·다른 순서 → 세션 재사용, 저장된 발언 순서 유지
        status, second = self.request(
            "POST",
            "/api/reader-debate",
            {
                "work_id": str(pid),
                "persona_ids": list(reversed(DEBATE_PERSONAS)),
                "user_message": "두 번째 질문",
            },
        )
        self.assertEqual(status, 200, second)
        self.assertEqual(first.get("session_id"), second.get("session_id"))
        self.assertEqual(second.get("round_number"), 2)
        self.assertEqual(second.get("persona_order"), DEBATE_PERSONAS)
        self.assertEqual(
            [item["persona_id"] for item in (second.get("replies") or [])],
            DEBATE_PERSONAS,
        )
        # round2 prompt remembers round1
        last_prompt = self.calls[-1]["prompt"]
        self.assertIn("첫 질문", last_prompt)

        ids_csv = ",".join(reversed(DEBATE_PERSONAS))
        status, history = self.request(
            "GET",
            f"/api/reader-debate/history?work_id={pid}&persona_ids={ids_csv}",
        )
        self.assertEqual(status, 200, history)
        self.assertEqual(history.get("session_id"), first["session_id"])
        rounds = history.get("rounds") or []
        self.assertEqual(len(rounds), 2)
        self.assertEqual(rounds[0]["round_number"], 1)
        self.assertEqual(rounds[1]["round_number"], 2)
        self.assertEqual(rounds[0]["messages"][0]["speaker_type"], "user")
        self.assertEqual(rounds[0]["messages"][0]["message"], "첫 질문")
        self.assertEqual(len(rounds[0]["messages"]), 4)  # user + 3 personas

    def test_gemini_failure_skips_one_persona(self) -> None:
        pid = self._make_project()

        def _flaky(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system or ""})
            if "로맨스·로판 서사파" in (system or ""):
                raise gemini_client.GeminiError("quota exceeded 429")
            return f"ok-{len(self.calls)}"

        gemini_client.generate_text = _flaky  # type: ignore[method-assign]
        status, result = self.request(
            "POST",
            "/api/reader-debate",
            {
                "work_id": str(pid),
                "persona_ids": DEBATE_PERSONAS,
                "user_message": "스킵 테스트",
            },
        )
        self.assertEqual(status, 200, result)
        replies = result.get("replies") or []
        self.assertEqual(len(replies), 3)
        self.assertTrue(replies[0]["ok"])
        self.assertTrue(replies[1]["skipped"])
        self.assertTrue(replies[2]["ok"])
        self.assertEqual(len(self.calls), 3)

        status, history = self.request(
            "GET",
            f"/api/reader-debate/history?work_id={pid}&persona_ids={','.join(DEBATE_PERSONAS)}",
        )
        self.assertEqual(status, 200, history)
        # user + 2 personas (skipped not stored)
        self.assertEqual(len(history["rounds"][0]["messages"]), 3)

    def test_rejects_too_few_personas(self) -> None:
        pid = self._make_project()
        status, result = self.request(
            "POST",
            "/api/reader-debate",
            {
                "work_id": str(pid),
                "persona_ids": DEBATE_PERSONAS[:2],
                "user_message": "안 됨",
            },
        )
        self.assertEqual(status, 400, result)


@unittest.skipUnless(
    REAL_DB.exists() and gemini_client.is_configured(),
    "real DB + Gemini required",
)
class ReaderDebateLiveProject11Tests(unittest.TestCase):
    """Project 11 copy: 3 personas, two rounds, print replies for manual review."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        self.original_generate = gemini_client.generate_text
        dst = Path(self.temporary_directory.name) / "data"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REAL_DB, dst / "supertory.sqlite3")
        for suffix in ("-wal", "-shm"):
            extra = Path(str(REAL_DB) + suffix)
            if extra.exists():
                shutil.copy2(extra, dst / (REAL_DB.name + suffix))
        app.DATA_DIR = dst
        app.DATABASE_PATH = dst / "supertory.sqlite3"
        app.initialise_database()
        with app.database() as connection:
            row = connection.execute(
                "SELECT id, title FROM project WHERE id = 11 AND deleted_at IS NULL"
            ).fetchone()
        if row is None:
            self.skipTest("project 11 not found in data/supertory.sqlite3")
        self.project_title = str(row["title"] or "")
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        gemini_client.generate_text = self.original_generate  # type: ignore[method-assign]
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, object]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=300
        )
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = raw
        return response.status, data

    def test_live_project_11_three_voices_and_session_resume(self) -> None:
        print(f"\n[live debate] project 11: {self.project_title}")
        status, first = self.request(
            "POST",
            "/api/reader-debate",
            {
                "work_id": "11",
                "persona_ids": DEBATE_PERSONAS,
                "user_message": "이번 화 어떻게 봤어요?",
            },
        )
        self.assertEqual(status, 200, first)
        replies = first.get("replies") or []
        self.assertEqual(len(replies), 3)
        ok_replies = [item for item in replies if item.get("ok")]
        self.assertGreaterEqual(len(ok_replies), 2, first)
        print("\n=== Round 1 ===")
        for item in replies:
            print(f"\n[{item.get('persona_name')}] ok={item.get('ok')}")
            print(item.get("message") or item.get("error") or "")

        status, second = self.request(
            "POST",
            "/api/reader-debate",
            {
                "work_id": "11",
                "persona_ids": DEBATE_PERSONAS,
                "user_message": "그럼 다음에 뭐가 더 궁금해요?",
            },
        )
        self.assertEqual(status, 200, second)
        self.assertEqual(second.get("session_id"), first.get("session_id"))
        self.assertEqual(second.get("round_number"), 2)
        print("\n=== Round 2 (session resumed) ===")
        for item in second.get("replies") or []:
            print(f"\n[{item.get('persona_name')}] ok={item.get('ok')}")
            print(item.get("message") or item.get("error") or "")

        status, history = self.request(
            "GET",
            "/api/reader-debate/history?work_id=11&persona_ids="
            + ",".join(DEBATE_PERSONAS),
        )
        self.assertEqual(status, 200, history)
        self.assertEqual(len(history.get("rounds") or []), 2)
        # Round 2 prompts should remember round 1 via stored history —
        # at least one ok reply mentioning prior thread is soft; assert session link.
        self.assertEqual(history.get("session_id"), first.get("session_id"))


if __name__ == "__main__":
    unittest.main()
