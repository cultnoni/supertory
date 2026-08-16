"""캐릭터 가상 논쟁 (mode=chardebate) — 설정집 + tracked_facts."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client

ROOT = Path(__file__).resolve().parents[1]

CHARS = [
    {
        "name": "서연",
        "personality": "짧게 끊고, 쉽게 굽히지 않는다. 반말.",
        "tone": "짧게 끊고 반말",
        "currentFacts": "신체상태 · 왼팔 · 붕대를 감은 부상",
    },
    {
        "name": "묵연",
        "personality": "낮고 느린 존댓말. 감정을 잘 드러내지 않는다.",
        "tone": "낮고 느린 존댓말",
        "currentFacts": "소지품 · 검 · 칼집에 넣어 둠",
    },
]


class CharacterDebateTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_ui_in_character_chat_not_helper_dropdown(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        help_i = html.find('label="토리와 구상해요"')
        write_i = html.find('label="토리와 함께 써요"')
        self.assertGreater(help_i, 0)
        self.assertGreater(write_i, help_i)
        self.assertNotIn('value="chardebate"', html)
        self.assertNotIn("캐릭터 가상 논쟁", html[help_i:write_i])
        picker = html.find('id="toryChatCharacterPicker"')
        self.assertGreater(picker, 0)
        chunk = html[picker: picker + 3500]
        self.assertIn('data-char-list-mode="chat"', chunk)
        self.assertIn('data-char-list-mode="sim"', chunk)
        self.assertIn("1:1 대화", chunk)
        self.assertIn("시뮬레이션", chunk)
        self.assertIn('id="toryChatCharacterSimPane"', chunk)
        self.assertIn('id="toryChatCharacterStart"', chunk)
        self.assertIn("function buildCharacterDebatePrompt", app_js)
        self.assertIn("function setCharListMode", app_js)
        self.assertIn("CHAR_DEBATE_MAX", app_js)
        self.assertNotIn("function openCharDebateModal", app_js)

    def test_prompt_keeps_facts_and_no_core_identity(self) -> None:
        prompt = app.SuperToryHandler._build_character_debate_prompt(
            CHARS,
            "서로의 비밀이 드러난 순간",
        )
        self.assertIn("[현재 작업]", prompt)
        self.assertIn("서로의 비밀이 드러난 순간", prompt)
        self.assertIn("서연", prompt)
        self.assertIn("묵연", prompt)
        self.assertIn("왼팔", prompt)
        self.assertIn("말투가 서로 섞이지", prompt)
        self.assertNotIn("Core Identity", prompt)

    def test_tracked_facts_match_by_name(self) -> None:
        facts = [
            {"category": "신체상태", "subject": "서연", "attribute": "왼팔", "value": "부상"},
            {"category": "관계", "subject": "다른사람", "attribute": "약혼", "value": "파기"},
        ]
        mine = app.SuperToryHandler._format_tracked_facts_for_character(facts, "서연", ["서연이"])
        other = app.SuperToryHandler._format_tracked_facts_for_character(facts, "묵연")
        self.assertIn("부상", mine)
        self.assertEqual(other, "기록된 현재 상태 없음")

    def test_dry_run_uses_task_prompt(self) -> None:
        task = app.SuperToryHandler._build_character_debate_prompt(
            CHARS,
            "목숨을 건 선택의 기로",
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "chardebate",
                "dry_run": True,
                "project_title": "논쟁",
                "task_prompt": task,
                "characters_info": CHARS,
                "scenario": "목숨을 건 선택의 기로",
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("목숨을 건 선택의 기로", full)
        self.assertIn("왼팔", full)
        self.assertNotIn("[프로젝트 누적 정보", full)
        self.assertNotIn("현재 원고:\n", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_two_voices_and_injury(self) -> None:
        task = app.SuperToryHandler._build_character_debate_prompt(
            CHARS,
            "목숨을 건 선택의 기로",
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "chardebate",
                "project_title": "논쟁",
                "task_prompt": task,
                "characters_info": CHARS,
                "scenario": "목숨을 건 선택의 기로",
            },
        )
        self.assertEqual(status, 200, result)
        text = result.get("text") or ""
        self.assertIn("서연", text)
        self.assertIn("묵연", text)
        self.assertRegex(text, r"(팔|붕대|부상|상처|왼손|다친)")
        self.assertGreaterEqual(text.count("서연"), 3, text)
        self.assertGreaterEqual(text.count("묵연"), 3, text)
