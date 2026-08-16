"""Glump ER lucky sentence: draws, session redraw cap, force-choice."""

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

SENTENCES = [
    "기둥이 작게 숨을 쉬었다.",
    "편지가 아직 체온을 품고 있었다.",
    "발소리가 한 걸음 가까워졌다.",
    "이 문장은 네 번째라 나오면 안 된다.",
]


class GlumpLuckySentenceTests(unittest.TestCase):
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
        self.calls: list[object] = []
        self.i = 0
        self._orig_generate = gemini_client.generate_text

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system})
            text = SENTENCES[self.i % len(SENTENCES)]
            self.i += 1
            return text

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
            {"title": "럭키 테스트", "main_genre": "romfant", "sub_genre": "high"},
        )
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def _draw(self, pid: int, session_id: str) -> tuple[int, object]:
        return self.request(
            "POST",
            "/api/glump/lucky-sentence",
            {
                "work_id": str(pid),
                "session_id": session_id,
                "episode_content": SAMPLE_EPISODE,
            },
        )

    def test_migration_042_creates_table(self) -> None:
        with app.database() as connection:
            versions = {
                row[0]
                for row in connection.execute("SELECT version FROM schema_migration")
            }
            self.assertIn(42, versions)
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("glump_lucky_draws", tables)

    def test_prompt_reuses_tory_core(self) -> None:
        system, user = app._lucky_sentence_prompt("로맨스 판타지", SAMPLE_EPISODE)
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertTrue(system.startswith("[Tory Core Identity]"))
        self.assertIn(core, system)
        self.assertIn("로맨스 판타지", system)
        self.assertIn("여주는 연회장", system)
        self.assertIn("20~40자", user)
        self.assertIn("한 문장", user)

    def test_short_episode_is_400(self) -> None:
        pid = self._make_project()
        status, data = self.request(
            "POST",
            "/api/glump/lucky-sentence",
            {
                "work_id": str(pid),
                "session_id": "s1",
                "episode_content": "짧음",
            },
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(
            data.get("error"),
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요",
        )
        self.assertEqual(self.calls, [])

    def test_fourth_draw_force_choice_and_new_session_resets(self) -> None:
        pid = self._make_project()
        sid = "lucky-session-a"
        seen: list[str] = []
        for n in range(1, 4):
            status, data = self._draw(pid, sid)
            self.assertEqual(status, 200, data)
            self.assertFalse(data.get("force_choice"))
            self.assertEqual(data.get("draw_count"), n)
            seen.append(data["sentence"])
        self.assertEqual(len(self.calls), 3)
        status, fourth = self._draw(pid, sid)
        self.assertEqual(status, 200, fourth)
        self.assertTrue(fourth.get("force_choice"))
        self.assertEqual(fourth.get("previous_options"), seen[:3])
        self.assertNotIn("sentence", fourth)
        self.assertEqual(len(self.calls), 3)
        with app.database() as connection:
            n_log = connection.execute(
                "SELECT COUNT(*) FROM glump_tool_logs WHERE tool_id = 'lucky_sentence'"
            ).fetchone()[0]
            n_draw = connection.execute(
                "SELECT COUNT(*) FROM glump_lucky_draws WHERE session_id = ?",
                (sid,),
            ).fetchone()[0]
        self.assertEqual(int(n_log), 1)
        self.assertEqual(int(n_draw), 3)
        status, fresh = self._draw(pid, "lucky-session-b")
        self.assertEqual(status, 200, fresh)
        self.assertFalse(fresh.get("force_choice"))
        self.assertEqual(fresh.get("draw_count"), 1)
        self.assertEqual(fresh.get("sentence"), SENTENCES[3])
        self.assertEqual(len(self.calls), 4)
        with app.database() as connection:
            n_log = connection.execute(
                "SELECT COUNT(*) FROM glump_tool_logs WHERE tool_id = 'lucky_sentence'"
            ).fetchone()[0]
        self.assertEqual(int(n_log), 2)

    def test_ui_plays_tori_lucky_intro(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        idle = root / "assets" / "glump" / "tori-lucky-idle.png"
        anim = root / "assets" / "glump" / "tori-lucky.gif"
        self.assertIn('id="glumpErLuckyTori"', html)
        self.assertIn("/assets/glump/tori-lucky.gif", html)
        self.assertIn("function playGlumpLuckyToriIntro()", js)
        self.assertIn("playGlumpLuckyToriIntro()", js)
        self.assertTrue(idle.is_file())
        self.assertTrue(anim.is_file())
