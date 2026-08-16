"""Glump ER character 1:1 interrogation: questions, persona answers, hint cards."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client

SAMPLE_EPISODE = (
    "여주는 연회장 기둥 뒤에 숨은 채 숨소리를 죽였다. 남주는 이미 그녀의 가짜 신분을 "
    "알아챈 것 같았고, 황실 근위대가 입구를 하나씩 잠그기 시작했다. "
    "방금 전 그녀가 훔친 편지에는 반란의 날짜가 적혀 있었다."
)

QUESTIONS_JSON = json.dumps(
    {
        "questions": [
            "너 지금 도망칠 거야, 맞설 거야?",
            "왜 그 사람한테 거짓말했어?",
        ]
    },
    ensure_ascii=False,
)

HINTS_JSON = json.dumps(
    [
        {"title": "맞서기", "description": "숨지 않고 신분을 밝히겠다는 뜻이 분명하다."},
        {"title": "거짓의 이유", "description": "거짓말은 생존이지 배신이 아니었다."},
    ],
    ensure_ascii=False,
)


class GlumpInterrogationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.calls: list[dict] = []
        self._orig_generate = gemini_client.generate_text

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system or ""})
            blob = f"{system or ''}\n{prompt}"
            if "Character Roleplay Chat" in blob:
                return "난 도망치지 않아. 그 사람 앞에서 진실을 말할게."
            if "방향 힌트" in prompt:
                return HINTS_JSON
            if "추궁할 날카로운 질문" in prompt:
                return QUESTIONS_JSON
            return "unexpected"

        gemini_client.generate_text = _fake  # type: ignore[method-assign]

    def tearDown(self) -> None:
        gemini_client.generate_text = self._orig_generate  # type: ignore[method-assign]
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
            "127.0.0.1", self.server.server_port, timeout=15
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
            {"title": "청문회 테스트", "main_genre": "romfant", "sub_genre": "high"},
        )
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def _make_protagonist(self, pid: int, name: str = "리아") -> int:
        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/characters",
            {"name": name},
        )
        self.assertEqual(status, 201, created)
        character_id = int(created["id"])
        status, detail = self.request("GET", f"/api/characters/{character_id}")
        self.assertEqual(status, 200, detail)
        status, saved = self.request(
            "PUT",
            f"/api/characters/{character_id}",
            {
                "name": name,
                "role": "protagonist",
                "short_description": "가짜 신분으로 연회에 잠입한 여주",
                "profile_md": "짧게 말하고, 쉽게 굽히지 않는다.",
                "row_version": detail["character"]["row_version"],
            },
        )
        self.assertEqual(status, 200, saved)
        return character_id

    def test_migration_043_creates_table(self) -> None:
        with app.database() as connection:
            versions = {
                row[0]
                for row in connection.execute("SELECT version FROM schema_migration")
            }
            self.assertIn(43, versions)
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("glump_interrogation_sessions", tables)

    def test_prompt_reuses_tory_core_and_json_contract(self) -> None:
        system, user = app._interrogation_questions_prompt(
            "로맨스 판타지",
            SAMPLE_EPISODE,
            "리아",
            "이름: 리아 / 역할: 주인공",
        )
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertTrue(system.startswith("[Tory Core Identity]"))
        self.assertIn(core, system)
        self.assertIn("로맨스 판타지", system)
        self.assertIn("리아", system)
        self.assertIn("여주는 연회장", system)
        self.assertIn("추궁할 날카로운 질문", user)
        self.assertIn('"questions"', user)
        cards_system, cards_user = app._interrogation_summary_prompt(
            [{"question": "도망칠 거야?", "answer": "아니."}]
        )
        self.assertIn(core, cards_system)
        self.assertIn('"title"', cards_user)
        self.assertIn('"description"', cards_user)

    def test_short_episode_is_400(self) -> None:
        pid = self._make_project()
        status, data = self.request(
            "POST",
            "/api/glump/interrogation/start",
            {"work_id": str(pid), "episode_content": "짧음"},
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(
            data.get("error"),
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요",
        )
        self.assertEqual(self.calls, [])

    def test_missing_protagonist_is_400(self) -> None:
        pid = self._make_project()
        status, data = self.request(
            "POST",
            "/api/glump/interrogation/start",
            {"work_id": str(pid), "episode_content": SAMPLE_EPISODE},
        )
        self.assertEqual(status, 400, data)
        self.assertIn("주인공", data.get("error") or "")
        self.assertEqual(self.calls, [])

    def test_start_answer_summarize_keeps_character_persona(self) -> None:
        pid = self._make_project()
        self._make_protagonist(pid, "리아")
        status, start = self.request(
            "POST",
            "/api/glump/interrogation/start",
            {"work_id": str(pid), "episode_content": SAMPLE_EPISODE},
        )
        self.assertEqual(status, 200, start)
        self.assertEqual(start.get("character_name"), "리아")
        questions = start.get("questions") or []
        self.assertEqual(len(questions), 2)
        self.assertTrue(all(str(item).strip() for item in questions))
        session_id = start["session_id"]
        with app.database() as connection:
            row = connection.execute(
                "SELECT qa_json, status FROM glump_interrogation_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            qa = json.loads(row["qa_json"])
            self.assertEqual(row["status"], "in_progress")
            self.assertEqual(qa[0]["question"], questions[0])
            self.assertEqual(qa[0]["answer"], "")
        start_calls = len(self.calls)
        self.assertEqual(start_calls, 1)
        self.assertIn("[Tory Core Identity]", self.calls[0]["system"])
        self.assertNotIn("Character Roleplay Chat", self.calls[0]["system"])

        status, early = self.request(
            "POST",
            "/api/glump/interrogation/summarize",
            {"session_id": session_id},
        )
        self.assertEqual(status, 400, early)
        self.assertEqual(early.get("error"), "아직 답하지 않은 질문이 있어요")

        for index in range(len(questions)):
            status, answered = self.request(
                "POST",
                "/api/glump/interrogation/answer",
                {
                    "session_id": session_id,
                    "question_index": index,
                    "episode_content": SAMPLE_EPISODE,
                },
            )
            self.assertEqual(status, 200, answered)
            self.assertIn("도망치지 않아", answered.get("answer") or "")

        persona_calls = self.calls[start_calls:]
        self.assertEqual(len(persona_calls), 2)
        for item in persona_calls:
            self.assertIn("Character Roleplay Chat", item["system"])
            self.assertIn("리아", item["system"])
            self.assertIn("지정된 인물만 연기하세요", item["system"])
            self.assertIn("작가의 새 메시지", item["prompt"])

        status, summary = self.request(
            "POST",
            "/api/glump/interrogation/summarize",
            {"session_id": session_id},
        )
        self.assertEqual(status, 200, summary)
        cards = summary.get("hint_cards") or []
        self.assertEqual(len(cards), 2)
        self.assertTrue(
            all(item.get("title") and item.get("description") for item in cards)
        )
        with app.database() as connection:
            row = connection.execute(
                "SELECT status FROM glump_interrogation_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            n_log = connection.execute(
                "SELECT COUNT(*) FROM glump_tool_logs "
                "WHERE tool_id = 'character_interrogation'"
            ).fetchone()[0]
        self.assertEqual(row["status"], "summarized")
        self.assertEqual(int(n_log), 1)
        self.assertIn("방향 힌트", self.calls[-1]["prompt"])

    def test_ui_plays_tori_mic_intro(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        idle = root / "assets" / "glump" / "tori-mic-idle.png"
        anim = root / "assets" / "glump" / "tori-mic.gif"
        self.assertIn('id="glumpErInterrogationTori"', html)
        self.assertIn("/assets/glump/tori-mic.gif", html)
        self.assertIn("function playGlumpInterrogationToriIntro()", js)
        self.assertIn("playGlumpInterrogationToriIntro()", js)
        self.assertTrue(idle.is_file())
        self.assertTrue(anim.is_file())
