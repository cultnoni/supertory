"""Glump ER ping-pong relay: start, turns, 400-char check-in, end."""

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
    "방금 전 그녀가 훔친 편지에는 반란의 날짜가 적혀 있었다. 그런데 편지 마지막 줄의 필체가 "
    "죽은 오빠의 것과 너무 닮아 있었다."
)

SHORT_REPLIES = [
    "문이 살짝 열리며 발소리가 가까워졌다.",
    "그녀는 편지를 품 안에 숨긴 채 숨을 고였다.",
    "낮은 목소리가 기둥 너머에서 이름을 불렀다.",
    "발걸음이 멈추자 공기만 팽팽해졌다.",
]


class GlumpPingpongTests(unittest.TestCase):
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
        self.reply_i = 0
        self._orig_generate = gemini_client.generate_text

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system})
            text = SHORT_REPLIES[self.reply_i % len(SHORT_REPLIES)]
            self.reply_i += 1
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
            {"title": "핑퐁 테스트", "main_genre": "romfant", "sub_genre": "high"},
        )
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def _start(self, pid: int) -> str:
        status, data = self.request(
            "POST",
            "/api/glump/pingpong/start",
            {
                "work_id": str(pid),
                "episode_id": "3",
                "episode_content": SAMPLE_EPISODE,
            },
        )
        self.assertEqual(status, 200, data)
        sid = str(data.get("session_id") or "")
        self.assertTrue(sid)
        return sid

    def test_migration_041_creates_table(self) -> None:
        with app.database() as connection:
            versions = {
                row[0]
                for row in connection.execute("SELECT version FROM schema_migration")
            }
            self.assertIn(41, versions)
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("glump_pingpong_sessions", tables)

    def test_prompt_reuses_tory_core_not_reader(self) -> None:
        system, user = app._pingpong_turn_prompt(
            "로맨스 판타지",
            SAMPLE_EPISODE,
            "이름: 서윤 / 말투: 짧게 끊어 말한다",
            [{"speaker": "user", "text": "기둥이 흔들렸다."}],
        )
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertTrue(system.startswith("[Tory Core Identity]"))
        self.assertIn(core, system)
        self.assertIn("[Dynamic Context]", system)
        self.assertIn("서윤", system)
        self.assertIn("기둥이 흔들렸다.", system)
        self.assertIn("한두 문장만", user)
        self.assertNotIn("[Core Identity]\n당신은 '", system)

    def test_clip_reply_over_200(self) -> None:
        long = "다음이 다가왔다. " * 40
        clipped = app._clip_pingpong_reply(long)
        self.assertLessEqual(len(clipped), app.PINGPONG_REPLY_MAX)
        self.assertTrue(clipped)

    def test_short_episode_is_400(self) -> None:
        pid = self._make_project()
        status, data = self.request(
            "POST",
            "/api/glump/pingpong/start",
            {"work_id": str(pid), "episode_content": "짧음"},
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(
            data.get("error"),
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요",
        )
        self.assertEqual(self.calls, [])

    def test_start_does_not_call_gemini(self) -> None:
        pid = self._make_project()
        sid = self._start(pid)
        self.assertEqual(self.calls, [])
        with app.database() as connection:
            row = connection.execute(
                "SELECT status, turns_json, chars_since_checkin FROM glump_pingpong_sessions "
                "WHERE id = ?",
                (sid,),
            ).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertEqual(json.loads(row["turns_json"]), [])
        self.assertEqual(int(row["chars_since_checkin"]), 0)

    def test_four_short_turns_then_end(self) -> None:
        pid = self._make_project()
        sid = self._start(pid)
        tori: list[str] = []
        for i in range(4):
            status, data = self.request(
                "POST",
                "/api/glump/pingpong/turn",
                {"session_id": sid, "user_text": f"작가가 {i + 1}번째로 한 문장을 잇는다."},
            )
            self.assertEqual(status, 200, data)
            text = str(data.get("tori_text") or "")
            self.assertTrue(text)
            self.assertLessEqual(len(text), app.PINGPONG_REPLY_MAX)
            self.assertFalse(data.get("checkin"))
            tori.append(text)
            self.assertEqual(data.get("total_turns"), (i + 1) * 2)
        self.assertEqual(len(self.calls), 4)
        status, ended = self.request(
            "POST", "/api/glump/pingpong/end", {"session_id": sid}
        )
        self.assertEqual(status, 200, ended)
        final = ended.get("final_text") or ""
        self.assertIn("작가가 1번째로", final)
        self.assertIn(tori[0], final)
        self.assertIn(tori[-1], final)
        with app.database() as connection:
            row = connection.execute(
                "SELECT status FROM glump_pingpong_sessions WHERE id = ?",
                (sid,),
            ).fetchone()
            log = connection.execute(
                "SELECT tool_id FROM glump_tool_logs WHERE tool_id = 'pingpong_relay'"
            ).fetchone()
        self.assertEqual(row["status"], "ended")
        self.assertIsNotNone(log)

    def test_checkin_after_400_chars(self) -> None:
        pid = self._make_project()
        sid = self._start(pid)
        blob = "가" * 380
        status, data = self.request(
            "POST",
            "/api/glump/pingpong/turn",
            {"session_id": sid, "user_text": blob},
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data.get("checkin"))
        self.assertGreaterEqual(int(data.get("written_chars") or 0), 400)
        with app.database() as connection:
            row = connection.execute(
                "SELECT chars_since_checkin FROM glump_pingpong_sessions "
                "WHERE id = ?",
                (sid,),
            ).fetchone()
        self.assertEqual(int(row["chars_since_checkin"]), 0)

    def test_ended_and_missing_session(self) -> None:
        pid = self._make_project()
        sid = self._start(pid)
        status, _ = self.request(
            "POST",
            "/api/glump/pingpong/turn",
            {"session_id": sid, "user_text": "기둥이 흔들렸다."},
        )
        self.assertEqual(status, 200)
        status, _ = self.request("POST", "/api/glump/pingpong/end", {"session_id": sid})
        self.assertEqual(status, 200)
        status, data = self.request(
            "POST",
            "/api/glump/pingpong/turn",
            {"session_id": sid, "user_text": "한 문장 더."},
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(data.get("error"), "이미 끝난 세션이에요")
        status, missing = self.request(
            "POST",
            "/api/glump/pingpong/turn",
            {"session_id": "nope", "user_text": "한 문장."},
        )
        self.assertEqual(status, 404, missing)

    def test_ui_plays_tori_pingpong_intro(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        idle = root / "assets" / "glump" / "tori-pingpong-idle.png"
        anim = root / "assets" / "glump" / "tori-pingpong.gif"
        self.assertIn('id="glumpErPingpongTori"', html)
        self.assertIn('id="glumpErPingpongCanvas"', html)
        self.assertIn('id="glumpErPingpongTyping"', html)
        self.assertIn("/assets/glump/tori-pingpong.gif", html)
        self.assertIn("function playGlumpPingpongToriIntro()", js)
        self.assertIn("playGlumpPingpongToriIntro()", js)
        self.assertIn("function scrollGlumpPingpongCanvas(", js)
        self.assertIn('event.key === "Enter" && !event.shiftKey', js)
        self.assertTrue(idle.is_file())
        self.assertTrue(anim.is_file())
        from PIL import Image

        with Image.open(idle) as still:
            self.assertEqual(still.mode, "RGBA", idle.name)
            extrema = still.getchannel("A").getextrema()
            self.assertLess(extrema[0], 20, idle.name)
            self.assertGreater(extrema[1], 200, idle.name)
        with Image.open(anim) as webp:
            self.assertTrue(getattr(webp, "is_animated", False), "pingpong gif should animate")
            self.assertGreater(getattr(webp, "n_frames", 1), 20)
