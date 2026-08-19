"""Glump ER burnout diversions: view-only color / playlist / mood-board / word list."""

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
            {"name": "새벽 잉크", "hex": "#1B2430", "reason": "도망치는 밤의 침묵"},
            {"name": "촛불", "hex": "#E8B86D", "reason": "숨죽여 켠 작은 온기"},
            {"name": "편지 분홍", "hex": "c97a8a", "reason": "가짜 신분 아래 남은 체온"},
        ]
    },
    ensure_ascii=False,
)

PLAYLIST_JSON = json.dumps(
    {
        "playlist_title": "기둥 뒤에서 듣는 밤",
        "tracks": [
            {"title": "접힌 편지의 온기", "mood": "숨소리보다 작은 현"},
            {"title": "가짜 이름 왈츠", "mood": "발끝만 움직이는 무도회"},
            {"title": "근위대의 빗금", "mood": "멀리서 잠기는 문"},
            {"title": "오빠의 필체", "mood": "익숙해서 더 무서운 저음"},
            {"title": "도망치지 않는 숨", "mood": "마지막으로 고른 고요"},
        ],
    },
    ensure_ascii=False,
)

KEYWORDS_JSON = json.dumps(
    {"keywords": ["foggy castle corridor", "candlelight portrait"]},
    ensure_ascii=False,
)

WORDS_JSON = json.dumps(
    {
        "words": [
            {"word": "잠행", "nuance": "들키지 않으려는 걸음"},
            {"word": "가면", "nuance": "얼굴 위에 올린 신분"},
            {"word": "밀서", "nuance": "접히고 또 접힌 비밀"},
            {"word": "연회", "nuance": "웃음 아래 칼날"},
            {"word": "봉인", "nuance": "아직 열리면 안 되는 것"},
            {"word": "잔향", "nuance": "사라진 뒤에도 남는 숨"},
            {"word": "역광", "nuance": "정체를 가리는 빛"},
            {"word": "서약", "nuance": "쉽게 내리면 안 되는 입"},
            {"word": "미명", "nuance": "도망이 끝나는 시간"},
            {"word": "심연", "nuance": "편지가 가리키는 곳"},
        ]
    },
    ensure_ascii=False,
)

SENTENCES_JSON = json.dumps(
    {
        "sentences": [
            {"kind": "novel", "text": "가면 아래로 숨이 먼저 달아났다.", "note": "들키기 직전"},
            {"kind": "quote", "text": "진실은 늦게 올수록 더 짧게 말한다.", "note": "남는 한 줄"},
            {"kind": "wit", "text": "알리바이는 길수록 헐거워진다.", "note": "짧은 가시"},
            {"kind": "fact", "text": "촛농은 식기 전에 지문을 한 겹 더 입힌다.", "note": "놀라운 사실"},
            {"kind": "novel", "text": "그는 출구를 세고 나서야 인사를 했다.", "note": ""},
            {"kind": "quote", "text": "비밀은 입보다 손바닥에 더 오래 남는다.", "note": ""},
            {"kind": "wit", "text": "가짜 이름은 발음이 쉽다. 그래서 더 위험하다.", "note": ""},
            {"kind": "fact", "text": "낡은 봉랍은 열릴 때 소리보다 먼저 냄새를 낸다.", "note": ""},
            {"kind": "novel", "text": "밀서의 접힌 모서리가 장갑 안에서 따뜻했다.", "note": ""},
            {"kind": "wit", "text": "편지는 부치기 전까지가 가장 솔직하다.", "note": ""},
        ]
    },
    ensure_ascii=False,
)


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class GlumpBurnoutActivitiesTests(unittest.TestCase):
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
            joined = f"{system or ''}\n{prompt}"
            if "퍼스널컬러" in joined:
                return COLORS_JSON
            if "가상 플레이리스트" in joined or "실제 존재하는 노래" in joined:
                return PLAYLIST_JSON
            if "영어 키워드" in joined or "Pexels" in joined:
                return KEYWORDS_JSON
            if "문장집" in joined or "novel|quote|wit|fact" in joined:
                return SENTENCES_JSON
            if "한국어 단어" in joined or "뉘앙스" in joined:
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
        headers = {"Content-Type": "application/json"} if payload is not None else {}
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

    def _logged_tools(self, pid: int) -> list[str]:
        with app.database() as connection:
            rows = connection.execute(
                "SELECT tool_id FROM glump_tool_logs WHERE work_id = ? ORDER BY created_at",
                (str(pid),),
            ).fetchall()
        return [row["tool_id"] for row in rows]

    def test_burnout_diagnosis_still_offers_rest_choice(self) -> None:
        burnout = app.diagnose_glump("burnout")
        self.assertIsNone(burnout["recommended_tool"])
        self.assertTrue(burnout["show_rest_choice"])
        self.assertIn("300자", burnout["message"])
        pid = self._make_project()
        status, data = self.request(
            "POST",
            "/api/glump/diagnose",
            {"work_id": str(pid), "q1_answer": "burnout"},
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data.get("show_rest_choice"))

    def test_mood_color_prompt_and_parser(self) -> None:
        system, user = app._mood_color_prompt("로맨스 판타지", "리아", "이름: 리아")
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertTrue(system.startswith("[Tory Core Identity]"))
        self.assertIn(core, system)
        self.assertIn("리아", system)
        self.assertIn("퍼스널컬러", user)
        self.assertIn("성격", user)
        self.assertIn("외모", user)
        self.assertNotIn("이 작품의 분위기", user)
        self.assertIn('"colors"', user)
        colors = app._parse_mood_colors(COLORS_JSON)
        self.assertEqual(len(colors), 3)
        self.assertEqual(colors[2]["hex"], "#C97A8A")
        self.assertEqual(app._normalise_hex_color("#abc"), "#AABBCC")
        with self.assertRaises(ValueError):
            app._parse_mood_colors("{}")

    def test_mood_playlist_prompt_forbids_real_songs(self) -> None:
        system, user = app._mood_playlist_prompt("로맨스 판타지", "리아", "이름: 리아")
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertIn(core, system)
        self.assertIn("실제 존재하는 노래", user)
        self.assertIn("가수", user)
        playlist = app._parse_mood_playlist(PLAYLIST_JSON)
        self.assertEqual(playlist["playlist_title"], "기둥 뒤에서 듣는 밤")
        self.assertEqual(len(playlist["tracks"]), 5)
        with self.assertRaises(ValueError):
            app._parse_mood_playlist('{"playlist_title": "x", "tracks": []}')

    def test_word_list_prompt_and_parser(self) -> None:
        system, user = app._word_list_prompt("로맨스 판타지")
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertIn(core, system)
        self.assertIn("10개", user)
        words = app._parse_word_list(WORDS_JSON)
        self.assertEqual(len(words), 10)
        self.assertEqual(words[0]["word"], "잠행")

    def test_sentence_list_prompt_and_parser(self) -> None:
        system, user = app._sentence_list_prompt("로맨스 판타지")
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertIn(core, system)
        self.assertIn("문장집", user)
        sentences = app._parse_sentence_list(SENTENCES_JSON)
        self.assertEqual(len(sentences), 10)
        self.assertEqual(sentences[0]["kind"], "novel")
        self.assertEqual(sentences[2]["kind"], "wit")

    def test_mood_color_endpoint_logs(self) -> None:
        pid = self._make_project()
        self._make_protagonist(pid)
        status, data = self.request(
            "POST",
            "/api/glump/mood-color",
            {"work_id": str(pid)},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data.get("colors") or []), 3)
        self.assertEqual(data.get("character_name"), "리아")
        self.assertEqual(self._logged_tools(pid), ["mood_color"])
        self.assertTrue(self.calls)

    def test_mood_playlist_endpoint_logs(self) -> None:
        pid = self._make_project()
        self._make_protagonist(pid)
        status, data = self.request(
            "POST",
            "/api/glump/mood-playlist",
            {"work_id": str(pid)},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data.get("tracks") or []), 5)
        self.assertTrue(data.get("playlist_title"))
        self.assertEqual(self._logged_tools(pid), ["mood_playlist"])

    def test_word_list_get_logs(self) -> None:
        pid = self._make_project()
        status, data = self.request("GET", f"/api/glump/word-list?work_id={pid}")
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data.get("words") or []), 10)
        self.assertEqual(self._logged_tools(pid), ["word_list"])

    def test_sentence_list_get_logs(self) -> None:
        pid = self._make_project()
        status, data = self.request("GET", f"/api/glump/sentence-list?work_id={pid}")
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data.get("sentences") or []), 10)
        self.assertEqual(self._logged_tools(pid), ["sentence_list"])

    def test_mood_board_empty_pexels_key_is_400_not_500(self) -> None:
        pid = self._make_project()
        with patch.object(app, "_pexels_api_key", return_value=""):
            status, data = self.request(
                "POST",
                "/api/glump/mood-board",
                {"work_id": str(pid), "keywords": ["foggy forest"]},
            )
        self.assertEqual(status, 400, data)
        self.assertNotEqual(status, 500)
        self.assertEqual(data.get("error"), app.MOOD_BOARD_EMPTY_KEY_MSG)
        self.assertEqual(self.calls, [])
        self.assertEqual(self._logged_tools(pid), [])

    def test_mood_board_with_keywords_fetches_photos_and_logs(self) -> None:
        pid = self._make_project()
        fake_payload = {
            "photos": [
                {
                    "photographer": "Example Photographer",
                    "alt": "fog",
                    "src": {
                        "large": "https://images.example.com/fog-large.jpg",
                        "medium": "https://images.example.com/fog-medium.jpg",
                    },
                }
            ]
        }

        def _fake_urlopen(request, timeout=12):
            header_blob = " ".join(
                f"{key}:{value}" for key, value in (request.header_items() or [])
            )
            self.assertIn("test-pexels-key", header_blob)
            self.assertIn("api.pexels.com", request.full_url)
            return _FakeHttpResponse(fake_payload)

        with patch.object(app, "_pexels_api_key", return_value="test-pexels-key"):
            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                status, data = self.request(
                    "POST",
                    "/api/glump/mood-board",
                    {"work_id": str(pid), "keywords": ["foggy forest path"]},
                )
        self.assertEqual(status, 200, data)
        self.assertEqual(self.calls, [])
        photos = data.get("photos") or []
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]["url"], "https://images.example.com/fog-large.jpg")
        self.assertEqual(photos[0]["photographer"], "Example Photographer")
        self.assertEqual(self._logged_tools(pid), ["mood_board"])

