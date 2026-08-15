"""Glump ER low-pressure diversions: colors, playlist, mood board, word list."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import gemini_client

COLORS_JSON = json.dumps(
    {
        "colors": [
            {"name": "새벽 잉크", "hex": "#1C2430", "reason": "숨죽인 연회의 그림자"},
            {"name": "촛농 크림", "hex": "#F3E2C6", "reason": "가짜 미소의 온기"},
            {"name": "편지 적갈", "hex": "#8B3A3A", "reason": "아직 식지 않은 비밀"},
        ]
    },
    ensure_ascii=False,
)

PLAYLIST_JSON = json.dumps(
    {
        "playlist_title": "기둥 뒤의 숨",
        "tracks": [
            {"title": "닫히지 않는 문", "mood": "발소리가 가까워지는 긴장"},
            {"title": "촛불이 먼저 안다", "mood": "들킬 것 같은 온기"},
            {"title": "가짜 이름 왈츠", "mood": "연회장의 느린 위태로움"},
            {"title": "봉인된 봉투", "mood": "편지가 체온을 품은 느낌"},
            {"title": "새벽 근위대", "mood": "출구가 하나씩 잠기는 리듬"},
        ],
    },
    ensure_ascii=False,
)

KEYWORDS_JSON = json.dumps(
    {"keywords": ["candlelit ballroom", "hidden letter fog"]},
    ensure_ascii=False,
)

WORDS_JSON = json.dumps(
    {
        "words": [
            {"word": "위장", "nuance": "신분을 덮는 얇은 막"},
            {"word": "연회", "nuance": "웃음 아래 칼날이 숨은 자리"},
            {"word": "밀서", "nuance": "손바닥 체온이 남는 종이"},
            {"word": "근위", "nuance": "출구를 하나씩 잠그는 발소리"},
            {"word": "가명", "nuance": "혀에 얹으면 살짝 씁쓸한 이름"},
            {"word": "촛농", "nuance": "들키기 직전의 노란 온기"},
            {"word": "기둥", "nuance": "숨을 죽이는 차가운 등받이"},
            {"word": "서찰", "nuance": "아직 접힌 반란의 날짜"},
            {"word": "야행", "nuance": "발자국을 지우는 밤의 보폭"},
            {"word": "참칭", "nuance": "빌린 목덜미의 무게"},
        ]
    },
    ensure_ascii=False,
)


class GlumpDiversionTests(unittest.TestCase):
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
            if "퍼스널컬러" in blob:
                return COLORS_JSON
            if "가상 플레이리스트" in blob or "실제 존재하는 노래" in blob:
                return PLAYLIST_JSON
            if "Pexels" in blob or "영어 키워드" in blob:
                return KEYWORDS_JSON
            if "한국어 단어 10개" in blob:
                return WORDS_JSON
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
            {"title": "딴짓 테스트", "main_genre": "romfant", "sub_genre": "high"},
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

    def _log_count(self, work_id: int, tool_id: str) -> int:
        with app.database() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM glump_tool_logs "
                "WHERE work_id = ? AND tool_id = ?",
                (str(work_id), tool_id),
            ).fetchone()
        return int(row["n"] if row else 0)

    def test_prompts_reuse_tory_core(self) -> None:
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        color_sys, color_user = app._mood_color_prompt("로맨스 판타지", "리아", "이름: 리아")
        self.assertTrue(color_sys.startswith("[Tory Core Identity]"))
        self.assertIn(core, color_sys)
        self.assertIn("퍼스널컬러", color_user)
        play_sys, play_user = app._mood_playlist_prompt("로맨스 판타지", "리아", "이름: 리아")
        self.assertIn(core, play_sys)
        self.assertIn("실제 존재하는 노래", play_user)
        word_sys, word_user = app._word_list_prompt("로맨스 판타지")
        self.assertIn(core, word_sys)
        self.assertIn("한국어 단어 10개", word_user)

    def test_mood_color_and_playlist(self) -> None:
        pid = self._make_project()
        self._make_protagonist(pid)
        status, data = self.request("POST", "/api/glump/mood-color", {"work_id": pid})
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data.get("colors") or []), 3)
        self.assertEqual(data["colors"][0]["hex"], "#1C2430")
        self.assertEqual(self._log_count(pid, "mood_color"), 1)

        status, data = self.request("POST", "/api/glump/mood-playlist", {"work_id": pid})
        self.assertEqual(status, 200, data)
        self.assertEqual(data.get("playlist_title"), "기둥 뒤의 숨")
        self.assertEqual(len(data.get("tracks") or []), 5)
        self.assertEqual(self._log_count(pid, "mood_playlist"), 1)

    def test_word_list_get(self) -> None:
        pid = self._make_project()
        status, data = self.request("GET", f"/api/glump/word-list?work_id={pid}")
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data.get("words") or []), 10)
        self.assertEqual(data["words"][0]["word"], "위장")
        self.assertEqual(self._log_count(pid, "word_list"), 1)

        status, data = self.request("GET", "/api/glump/word-list")
        self.assertEqual(status, 400, data)
        self.assertIn("작품", str(data.get("error") or data))

    def test_mood_board_empty_key_is_not_500(self) -> None:
        pid = self._make_project()
        with patch.object(app, "_pexels_api_key", return_value=""):
            status, data = self.request(
                "POST",
                "/api/glump/mood-board",
                {"work_id": pid, "keywords": ["foggy forest"]},
            )
        self.assertEqual(status, 400, data)
        self.assertNotEqual(status, 500)
        self.assertEqual(data.get("error"), app.MOOD_BOARD_EMPTY_KEY_MSG)
        self.assertEqual(self._log_count(pid, "mood_board"), 0)

    def test_mood_board_fetches_photos(self) -> None:
        pid = self._make_project()
        sample = {
            "photos": [
                {
                    "photographer": "Ada",
                    "alt": "fog",
                    "src": {"large": "https://images.pexels.com/example.jpg"},
                }
            ]
        }

        class _FakeResponse:
            def read(self) -> bytes:
                return json.dumps(sample).encode("utf-8")

            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        with patch.object(app, "_pexels_api_key", return_value="test-key"):
            with patch("urllib.request.urlopen", return_value=_FakeResponse()):
                status, data = self.request(
                    "POST",
                    "/api/glump/mood-board",
                    {"work_id": pid, "keywords": ["foggy forest"]},
                )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["photos"][0]["url"], "https://images.pexels.com/example.jpg")
        self.assertEqual(data["photos"][0]["photographer"], "Ada")
        self.assertEqual(self._log_count(pid, "mood_board"), 1)


if __name__ == "__main__":
    unittest.main()
