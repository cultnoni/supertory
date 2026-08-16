"""Virtual reader 1:1 chat: prompts, list/history APIs, POST /api/reader-chat."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


class ReaderChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        self.original_generate = gemini_client.generate_text
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.captured: dict[str, object] = {}

        def _fake_generate(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.captured["prompt"] = prompt
            self.captured["system"] = system
            return "이렇게 쌓아놓고 이 정도로 끝나면 섭섭하지. 여주가 직접 되갚는 장면이 더 필요해."

        gemini_client.generate_text = _fake_generate  # type: ignore[method-assign]

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
            "127.0.0.1", self.server.server_port, timeout=30
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
            {"title": "사이다 테스트", "main_genre": "romfant", "sub_genre": "high"},
        )
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def test_persona_system_prompt_is_only_this_reader(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT * FROM virtual_reader_personas WHERE id = ?",
                ("roppan_cider",),
            ).fetchone()
        prompt = app._reader_persona_system_prompt(dict(row))
        self.assertIn("당신은 '로맨스·로판 사이다파'이라는 이름의 가상 독자입니다.", prompt)
        self.assertIn("[정체성]", prompt)
        self.assertIn("카타르시스", prompt)
        self.assertIn("[말투]", prompt)
        self.assertIn("[평가 우선순위]", prompt)
        self.assertIn("1. ", prompt)
        self.assertIn("주인공이 그 사이다를 스스로 만들어내는가", prompt)
        self.assertIn("[금지사항]", prompt)
        self.assertIn("[말투 예시]", prompt)
        self.assertIn("그대로 따라 말하지 말고", prompt)
        self.assertIn("1:1로 대화 중입니다", prompt)
        self.assertIn("AI라는 사실을 언급하지 말고", prompt)
        self.assertIn("이유를 함께 설명하세요", prompt)
        self.assertNotIn("토리 Core Identity", prompt)
        self.assertNotIn("당신은 '토리'입니다", prompt)
        self.assertNotIn("로판 서사파", prompt)
        self.assertNotIn("현대로맨스 설렘파", prompt)
        self.assertNotIn("discussion_attitude", prompt)

    def test_dynamic_context_includes_genre_and_optional_manuscript(self) -> None:
        pid = self._make_project()
        bare = app._reader_dynamic_context(pid)
        self.assertIn("메인 장르: 로판", bare)
        self.assertIn("서브 장르:", bare)
        self.assertNotIn("작가가 공유한 원고", bare)
        with_ms = app._reader_dynamic_context(pid, "빌런이 사과하고 끝났다.")
        self.assertIn("다음은 작가가 공유한 원고 내용입니다:", with_ms)
        self.assertIn("빌런이 사과하고 끝났다.", with_ms)
        self.assertNotIn("[Tory Core Identity]", with_ms)

    def test_list_personas_grouped_by_category(self) -> None:
        status, grouped = self.request("GET", "/api/reader-personas")
        self.assertEqual(status, 200, grouped)
        self.assertEqual(
            list(grouped.keys()),
            list(app.READER_PERSONA_CATEGORIES),
        )
        total = sum(len(grouped[key]) for key in grouped)
        self.assertEqual(total, 24)
        first = grouped["genre_specialist"][0]
        self.assertEqual(first["id"], "roppan_cider")
        self.assertIsInstance(first["criteria"], list)
        self.assertGreaterEqual(len(first["criteria"]), 1)

    def test_missing_persona_is_404(self) -> None:
        pid = self._make_project()
        status, result = self.request(
            "POST",
            "/api/reader-chat",
            {
                "work_id": str(pid),
                "persona_id": "not_a_real_reader",
                "user_message": "이 장면 어때?",
            },
        )
        self.assertEqual(status, 404, result)

    def test_post_reader_chat_saves_history(self) -> None:
        pid = self._make_project()
        status, chat = self.request(
            "POST",
            "/api/reader-chat",
            {
                "work_id": str(pid),
                "persona_id": "roppan_cider",
                "user_message": "이 장면 어때?",
                "episode_content": "빌런이 울면서 사과하고 끝났다.",
            },
        )
        self.assertEqual(status, 200, chat)
        self.assertEqual(chat.get("persona_name"), "로맨스·로판 사이다파")
        self.assertTrue(chat.get("session_id"))
        self.assertIn("섭섭하지", chat.get("reply") or "")
        system = str(self.captured.get("system") or "")
        self.assertIn("로맨스·로판 사이다파", system)
        self.assertIn("작가가 공유한 원고", system)
        self.assertNotIn("당신은 '토리'입니다", system)
        self.assertNotIn("로판 서사파", system)

        status, history = self.request(
            "GET",
            f"/api/reader-chat/history?work_id={pid}&persona_id=roppan_cider",
        )
        self.assertEqual(status, 200, history)
        self.assertEqual(history.get("session_id"), chat["session_id"])
        messages = history.get("messages") or []
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "이 장면 어때?")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertRegex(
            messages[0]["created_at"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$",
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_reader_chat_example(self) -> None:
        gemini_client.generate_text = self.original_generate  # type: ignore[method-assign]
        pid = self._make_project()
        status, chat = self.request(
            "POST",
            "/api/reader-chat",
            {
                "work_id": str(pid),
                "persona_id": "roppan_cider",
                "user_message": "이 장면 어때?",
                "episode_content": "빌런이 울면서 사과하고 끝났다. 여주는 아무 말도 하지 않았다.",
            },
        )
        self.assertEqual(status, 200, chat)
        self.assertEqual(chat.get("persona_name"), "로맨스·로판 사이다파")
        self.assertGreater(len(str(chat.get("reply") or "")), 10)

    def test_avatar_pngs_are_served_for_every_persona(self) -> None:
        status, grouped = self.request("GET", "/api/reader-personas")
        self.assertEqual(status, 200, grouped)
        ids = []
        for key in app.READER_PERSONA_CATEGORIES:
            ids.extend(item["id"] for item in grouped[key])
        self.assertEqual(len(ids), 24)
        missing = []
        for persona_id in ids:
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_port, timeout=10
            )
            connection.request("GET", f"/assets/reader_avatars/{persona_id}.png")
            response = connection.getresponse()
            code = response.status
            body = response.read()
            connection.close()
            if code != 200 or not body.startswith(b"\x89PNG"):
                missing.append(f"{persona_id}:{code}")
        self.assertEqual(missing, [])

    def test_reader_ui_is_wired_in_html_js(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="readerPersonaGrid"', html)
        self.assertIn('id="readerChatStartButton"', html)
        self.assertIn("대화 시작하기", html)
        self.assertIn("startReaderChatFromPicker", js)
        self.assertIn('id="readerPersonaAllButton"', html)
        self.assertIn('id="readerPersonaAllModal"', html)
        self.assertIn("openReaderPersonaAllModal", js)
        self.assertIn('id="readerChatForm"', html)
        self.assertIn('id="readerChatAttachButton"', html)
        self.assertIn("listExportEpisodes", js)
        self.assertIn("openReaderChatWithPersona", js)
        self.assertIn("setupReaderDebateUi", js)
        self.assertIn("data-reader-list-mode", html)
        self.assertIn('id="readerDebatePane"', html)
        self.assertIn("이 카테고리는 최대 5명까지예요", js)
        self.assertIn("최소 3명을 골라주세요", js)
        self.assertIn("/api/reader-chat", js)
        self.assertNotIn("가상 독자 대화는 다음 안내에서 이어서 만들어요.", html)
