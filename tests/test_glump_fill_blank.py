"""Glump ER fill-in-the-blank game: skeleton, submit, session resume table."""

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
    "죽은 오빠의 것과 너무 닮아 있었다. 여주는 편지를 다시 접으며 생각했다. "
    "지금 여기서 도망치면 살 수 있지만, 도망치는 순간 오빠의 진실을 영영 놓친다. "
    "등 뒤에서 익숙한 낮은 목소리가 들렸다. 네가 그 편지를 연 순간부터, 이미 판은 바뀌었다."
)

SAMPLE_SEGMENTS = [
    {"type": "fixed", "text": "기둥이 흔들렸다. "},
    {"type": "blank", "id": "b1", "hint": "인물이 방에 들어오는 동작 묘사"},
    {"type": "fixed", "text": "그가 낮은 목소리로 말했다. \""},
    {"type": "blank", "id": "b2", "hint": "감정이 드러나는 대사"},
    {"type": "fixed", "text": "\" "},
    {"type": "blank", "id": "b3", "hint": "여주의 짧은 반응"},
    {"type": "fixed", "text": " 편지는 아직 따뜻했다."},
]


def _skeleton_json(segments=None) -> str:
    return json.dumps(
        {"segments": segments if segments is not None else SAMPLE_SEGMENTS},
        ensure_ascii=False,
    )


class GlumpFillBlankTests(unittest.TestCase):
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
        self._orig_generate = gemini_client.generate_text
        self._orig_configured = gemini_client.is_configured

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system})
            return _skeleton_json()

        gemini_client.generate_text = _fake  # type: ignore[method-assign]
        gemini_client.is_configured = lambda: True  # type: ignore[method-assign]

    def tearDown(self) -> None:
        gemini_client.generate_text = self._orig_generate  # type: ignore[method-assign]
        gemini_client.is_configured = self._orig_configured  # type: ignore[method-assign]
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
            {"title": "빈칸 테스트", "main_genre": "romfant", "sub_genre": "high"},
        )
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def test_migration_040_creates_table(self) -> None:
        with app.database() as connection:
            versions = {
                row[0]
                for row in connection.execute("SELECT version FROM schema_migration")
            }
            self.assertIn(40, versions)
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("glump_fill_blank_sessions", tables)

    def test_prompt_reuses_tory_core_and_json_contract(self) -> None:
        system, user = app._fill_blank_skeleton_prompt(
            "로맨스 판타지", SAMPLE_EPISODE, "이름: 서윤 / 말투: 짧게 끊어 말한다"
        )
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertTrue(system.startswith("[Tory Core Identity]"))
        self.assertIn(core, system)
        self.assertIn("[Dynamic Context]", system)
        self.assertIn("로맨스 판타지", system)
        self.assertIn("서윤", system)
        self.assertIn("여주는 연회장", system)
        self.assertIn("[Task Instruction]", user)
        self.assertIn("빈칸은 3~5개", user)
        self.assertIn('"type": "blank"', user)
        self.assertIn("설정집", user)

    def test_assemble_joins_in_order(self) -> None:
        text = app._assemble_fill_blank_text(
            SAMPLE_SEGMENTS,
            {
                "b1": "문이 열리며 남주가 들어왔다.",
                "b2": "이제 숨지 마.",
                "b3": "여주는 편지를 더 꽉 쥐었다.",
            },
        )
        self.assertIn("기둥이 흔들렸다.", text)
        self.assertIn("문이 열리며 남주가 들어왔다.", text)
        self.assertIn("이제 숨지 마.", text)
        self.assertIn("여주는 편지를 더 꽉 쥐었다.", text)
        self.assertIn("편지는 아직 따뜻했다.", text)

    def test_assemble_rejects_empty_blank(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            app._assemble_fill_blank_text(
                SAMPLE_SEGMENTS, {"b1": "동작", "b2": "", "b3": "반응"}
            )
        self.assertEqual(str(ctx.exception), "모든 빈칸을 채워주세요")

    def test_short_episode_is_400(self) -> None:
        pid = self._make_project()
        status, data = self.request(
            "POST",
            "/api/glump/fill-blank/start",
            {
                "work_id": str(pid),
                "episode_id": "1",
                "episode_content": "너무 짧음",
            },
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(
            data.get("error"),
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요",
        )
        self.assertEqual(self.calls, [])

    def test_start_returns_segments_and_saves_session(self) -> None:
        pid = self._make_project()
        status, character = self.request(
            "POST", f"/api/projects/{pid}/characters", {"name": "서윤"}
        )
        self.assertEqual(status, 201, character)
        status, detail = self.request("GET", f"/api/characters/{character['id']}")
        self.assertEqual(status, 200, detail)
        status, _ = self.request(
            "PUT",
            f"/api/characters/{character['id']}",
            {
                "name": "서윤",
                "role": "protagonist",
                "profile_md": "짧게 끊어 말하며 존댓말을 안 쓴다.",
                "row_version": detail["character"]["row_version"],
            },
        )
        self.assertEqual(status, 200)
        status, data = self.request(
            "POST",
            "/api/glump/fill-blank/start",
            {
                "work_id": str(pid),
                "episode_id": "9",
                "episode_content": SAMPLE_EPISODE,
            },
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data.get("session_id"))
        segs = data.get("segments") or []
        self.assertEqual(len([item for item in segs if item["type"] == "blank"]), 3)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("서윤", str(self.calls[0]["system"]))
        self.assertIn("[Tory Core Identity]", str(self.calls[0]["system"]))
        with app.database() as connection:
            row = connection.execute(
                "SELECT work_id, episode_id, status FROM glump_fill_blank_sessions "
                "WHERE id = ?",
                (data["session_id"],),
            ).fetchone()
        self.assertEqual(str(row["work_id"]), str(pid))
        self.assertEqual(row["episode_id"], "9")
        self.assertEqual(row["status"], "in_progress")

    def test_submit_assembles_logs_and_rejects_empty(self) -> None:
        pid = self._make_project()
        status, started = self.request(
            "POST",
            "/api/glump/fill-blank/start",
            {
                "work_id": str(pid),
                "episode_id": "1",
                "episode_content": SAMPLE_EPISODE,
            },
        )
        self.assertEqual(status, 200, started)
        sid = started["session_id"]
        status, err = self.request(
            "POST",
            "/api/glump/fill-blank/submit",
            {"session_id": sid, "answers": {"b1": "동작", "b2": "", "b3": "반응"}},
        )
        self.assertEqual(status, 400, err)
        self.assertEqual(err.get("error"), "모든 빈칸을 채워주세요")
        status, done = self.request(
            "POST",
            "/api/glump/fill-blank/submit",
            {
                "session_id": sid,
                "answers": {
                    "b1": "문이 열리며 남주가 들어왔다.",
                    "b2": "이제 숨지 마.",
                    "b3": "여주는 편지를 더 꽉 쥐었다.",
                },
            },
        )
        self.assertEqual(status, 200, done)
        final = done.get("final_text") or ""
        self.assertTrue(final.startswith("기둥이 흔들렸다."))
        self.assertIn("이제 숨지 마.", final)
        self.assertTrue(final.endswith("편지는 아직 따뜻했다."))
        with app.database() as connection:
            sess = connection.execute(
                "SELECT status FROM glump_fill_blank_sessions WHERE id = ?",
                (sid,),
            ).fetchone()
            log = connection.execute(
                "SELECT tool_id FROM glump_tool_logs WHERE tool_id = 'fill_blank_game'"
            ).fetchone()
        self.assertEqual(sess["status"], "completed")
        self.assertIsNotNone(log)

    def test_retries_once_then_500(self) -> None:
        pid = self._make_project()
        n = {"count": 0}

        def _bad(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            n["count"] += 1
            return "이건 JSON이 아닙니다"

        gemini_client.generate_text = _bad  # type: ignore[method-assign]
        status, data = self.request(
            "POST",
            "/api/glump/fill-blank/start",
            {"work_id": str(pid), "episode_content": SAMPLE_EPISODE},
        )
        self.assertEqual(status, 500, data)
        self.assertEqual(data.get("error"), "다시 시도해주세요")
        self.assertEqual(n["count"], 2)

    def test_ui_plays_tori_puzzle_intro(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        idle = root / "assets" / "glump" / "tory-puzzle-idle.png"
        anim = root / "assets" / "glump" / "tory-puzzle.webp"
        self.assertIn('id="glumpErFillBlankTori"', html)
        self.assertIn("/assets/glump/tory-puzzle.webp", html)
        self.assertIn("function playGlumpFillBlankToriIntro()", js)
        self.assertIn("playGlumpFillBlankToriIntro()", js)
        self.assertTrue(idle.is_file())
        self.assertTrue(anim.is_file())
        from PIL import Image

        with Image.open(idle) as still:
            self.assertEqual(still.mode, "RGBA", idle.name)
            extrema = still.getchannel("A").getextrema()
            self.assertLess(extrema[0], 20, idle.name)
            self.assertGreater(extrema[1], 200, idle.name)
        with Image.open(anim) as webp:
            self.assertEqual(webp.format, "WEBP")
            self.assertTrue(getattr(webp, "is_animated", False), "puzzle webp should animate")
            self.assertGreater(getattr(webp, "n_frames", 1), 20)
