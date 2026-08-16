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

TAROT_JSON = json.dumps(
    {
        "cards": [
            {
                "name": "탑",
                "meaning": "리아가 연회장 기둥을 고른 이유? 무너질 걸 알고도 그 자리에 선 사람이에요. 점괘라기보다, 가짜 신분이 먼저 금이 가는 장면 스포일러에 가깝죠.",
            },
            {
                "name": "달",
                "meaning": "짧게 말하고 쉽게 안 굽히는 그 버릇, 사실 안개 속에서 길 찾는 습관입니다. 운명이 아니라 잠입 루틴이에요.",
            },
            {
                "name": "별",
                "meaning": "편지에 남은 체온처럼, 이 캐릭터는 어둠 속에서도 작은 출구를 하나 남겨 둡니다. 예언이 아니라 성격 버릇 해설입니다.",
            },
        ]
    },
    ensure_ascii=False,
)

def _naming_items(*pairs: tuple[str, str]) -> list[dict]:
    return [{"name": name, "nuance": nuance} for name, nuance in pairs]


NAMING_JSON = json.dumps(
    {
        "groups": [
            {
                "category": "동양풍 인명",
                "items": _naming_items(
                    ("서린", "안개처럼 남는 한자 이름"),
                    ("월하", "밤에 조용히 걷는 느낌"),
                    ("청운", "멀리 보는 기세"),
                    ("난주", "차갑게 꺾이지 않는 어감"),
                ),
            },
            {
                "category": "서양풍 인명",
                "items": _naming_items(
                    ("엘드리크", "낡은 성벽 같은 무게"),
                    ("미리엔", "은빛으로 스치는 이름"),
                    ("카론웰", "강을 건너는 저음"),
                    ("세레윈", "창가에 남는 잔향"),
                ),
            },
            {
                "category": "현대 인명",
                "items": _naming_items(
                    ("하도윤", "짧게 불리는 요즘 이름"),
                    ("문지아", "부드럽게 남는 받침"),
                    ("서민재", "담백한 두 음절 성"),
                    ("이나율", "가볍게 떨어지는 끝소리"),
                ),
            },
            {
                "category": "고대 무기·아이템",
                "items": _naming_items(
                    ("서리문 검", "얼어붙은 문턱을 여는 칼"),
                    ("잿빛 봉인환", "불을 삼킨 고리"),
                    ("파문 나침반", "길을 잃게 하는 바늘"),
                    ("침묵의 활대", "시위를 당기기 전 숨"),
                ),
            },
            {
                "category": "동서양 요괴·괴물",
                "items": _naming_items(
                    ("달그림자 구미", "꼬리 대신 달을 끄는 여우"),
                    ("우물손님", "이름을 물으면 빠지는 존재"),
                    ("안개서리고블린", "골목 안개로 위장하는 작은 손"),
                    ("뼈종 하피", "종소리로 길을 흩뜨리는 날개"),
                ),
            },
            {
                "category": "지명·장소",
                "items": _naming_items(
                    ("흰안개 나루", "배가 늦게 뜨는 마을"),
                    ("석등성", "밤마다 등이 먼저 깨어나는 성"),
                    ("검은이끼 숲", "발소리가 삼켜지는 숲"),
                    ("낮달 협곡", "정오에도 달이 보이는 골"),
                ),
            },
            {
                "category": "무협 용어",
                "items": _naming_items(
                    ("청운문", "하늘 기운을 빌린 문파"),
                    ("쇄월참", "달을 가르는 초식"),
                    ("현빙심법", "차갑게 가라앉히는 내공"),
                    ("팔방쇄진", "사방을 가두는 진법"),
                ),
            },
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
            if "타로 카드" in blob or "창작 놀이" in blob:
                return TAROT_JSON
            if "작명소" in blob or "동양풍 인명" in blob:
                return NAMING_JSON
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
        self.assertIn("성격", color_user)
        self.assertIn("외모", color_user)
        self.assertNotIn("이 작품의 분위기", color_user)
        play_sys, play_user = app._mood_playlist_prompt("로맨스 판타지", "리아", "이름: 리아")
        self.assertIn(core, play_sys)
        self.assertIn("실제 존재하는 노래", play_user)
        word_sys, word_user = app._word_list_prompt("로맨스 판타지")
        self.assertIn(core, word_sys)
        self.assertIn("한국어 단어 10개", word_user)
        tarot_sys, tarot_user = app._character_tarot_prompt(
            "로맨스 판타지", "리아", "이름: 리아"
        )
        self.assertTrue(tarot_sys.startswith("[Tory Core Identity]"))
        self.assertIn(core, tarot_sys)
        self.assertIn("타로 카드", tarot_user)
        self.assertIn("실제 점술이 아니라", tarot_user)
        self.assertIn("창작 놀이", tarot_user)
        self.assertNotIn("[Core Identity]\n당신은 '", tarot_sys)
        name_sys, name_user = app._naming_shop_prompt("로맨스 판타지")
        self.assertTrue(name_sys.startswith("[Tory Core Identity]"))
        self.assertIn(core, name_sys)
        self.assertIn("동양풍 인명", name_user)
        self.assertIn("무협 용어", name_user)
        self.assertIn("7개", name_user)
        self.assertIn("저작권", name_user)

    def test_mood_color_and_playlist(self) -> None:
        pid = self._make_project()
        self._make_protagonist(pid)
        status, data = self.request("POST", "/api/glump/mood-color", {"work_id": pid})
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data.get("colors") or []), 3)
        self.assertEqual(data["colors"][0]["hex"], "#1C2430")
        self.assertEqual(data.get("character_name"), "리아")
        self.assertEqual(data.get("character_role"), "protagonist")
        self.assertEqual(self._log_count(pid, "mood_color"), 1)
        self.assertTrue(self.calls)
        self.assertIn("리아", str(self.calls[0].get("system") or ""))
        self.assertIn("성격", self.calls[0]["prompt"])

        status, data = self.request("POST", "/api/glump/mood-playlist", {"work_id": pid})
        self.assertEqual(status, 200, data)
        self.assertEqual(data.get("playlist_title"), "기둥 뒤의 숨")
        self.assertEqual(len(data.get("tracks") or []), 5)
        self.assertEqual(self._log_count(pid, "mood_playlist"), 1)

    def test_mood_color_named_character(self) -> None:
        pid = self._make_project()
        self._make_protagonist(pid, "리아")
        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/characters",
            {"name": "카엘"},
        )
        self.assertEqual(status, 201, created)
        character_id = int(created["id"])
        status, detail = self.request("GET", f"/api/characters/{character_id}")
        self.assertEqual(status, 200, detail)
        status, saved = self.request(
            "PUT",
            f"/api/characters/{character_id}",
            {
                "name": "카엘",
                "role": "supporting",
                "short_description": "검은 머리의 근위, 시선이 차갑다",
                "profile_md": "낮게 말하고, 출구부터 센다.",
                "row_version": detail["character"]["row_version"],
            },
        )
        self.assertEqual(status, 200, saved)
        self.calls.clear()
        status, data = self.request(
            "POST",
            "/api/glump/mood-color",
            {"work_id": pid, "character_name": "카엘"},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data.get("character_name"), "카엘")
        self.assertEqual(data.get("character_role"), "supporting")
        self.assertIn("카엘", str(self.calls[-1].get("system") or ""))
        self.assertNotIn("리아", str(self.calls[-1].get("system") or ""))

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

    def test_character_tarot_uses_protagonist_and_logs(self) -> None:
        pid = self._make_project()
        self._make_protagonist(pid)
        status, data = self.request(
            "POST", "/api/glump/character-tarot", {"work_id": pid}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data.get("character_name"), "리아")
        cards = data.get("cards") or []
        self.assertEqual(len(cards), 3)
        self.assertEqual(cards[0]["name"], "탑")
        self.assertIn("가짜 신분", cards[0]["meaning"])
        self.assertEqual(self._log_count(pid, "character_tarot"), 1)
        self.assertTrue(self.calls)
        self.assertIn("[Tory Core Identity]", str(self.calls[0]["system"]))
        self.assertIn("리아", self.calls[0]["prompt"])

    def test_character_tarot_named_character(self) -> None:
        pid = self._make_project()
        self._make_protagonist(pid, "리아")
        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/characters",
            {"name": "카엘"},
        )
        self.assertEqual(status, 201, created)
        character_id = int(created["id"])
        status, detail = self.request("GET", f"/api/characters/{character_id}")
        self.assertEqual(status, 200, detail)
        status, saved = self.request(
            "PUT",
            f"/api/characters/{character_id}",
            {
                "name": "카엘",
                "role": "supporting",
                "short_description": "연회를 지키는 근위",
                "profile_md": "낮게 말하고, 출구부터 센다.",
                "row_version": detail["character"]["row_version"],
            },
        )
        self.assertEqual(status, 200, saved)
        status, data = self.request(
            "POST",
            "/api/glump/character-tarot",
            {"work_id": pid, "character_name": "카엘"},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data.get("character_name"), "카엘")
        self.assertIn("카엘", self.calls[-1]["prompt"])

    def test_character_tarot_needs_protagonist(self) -> None:
        pid = self._make_project()
        status, data = self.request(
            "POST", "/api/glump/character-tarot", {"work_id": pid}
        )
        self.assertEqual(status, 400, data)
        self.assertIn("주인공", str(data.get("error") or data))
        self.assertEqual(self._log_count(pid, "character_tarot"), 0)

    def test_character_tarot_parser_bounds(self) -> None:
        cards = app._parse_character_tarot(TAROT_JSON)
        self.assertEqual(len(cards), 3)
        two = json.dumps(
            {
                "cards": [
                    {"name": "바보", "meaning": "첫 잠입."},
                    {"name": "힘", "meaning": "굽히지 않는 입."},
                ]
            },
            ensure_ascii=False,
        )
        self.assertEqual(len(app._parse_character_tarot(two)), 2)
        with self.assertRaises(ValueError):
            app._parse_character_tarot('{"cards": [{"name": "탑", "meaning": "하나"}]}')

    def test_naming_shop_endpoint_and_parser(self) -> None:
        groups = app._parse_naming_shop(NAMING_JSON)
        self.assertEqual(len(groups), 7)
        self.assertEqual([item["category"] for item in groups], list(app.NAMING_SHOP_CATEGORIES))
        self.assertEqual(groups[0]["items"][0]["name"], "서린")
        self.assertEqual(groups[6]["category"], "무협 용어")
        self.assertEqual(groups[6]["items"][0]["name"], "청운문")
        pid = self._make_project()
        status, data = self.request("POST", "/api/glump/naming-shop", {"work_id": pid})
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data.get("groups") or []), 7)
        self.assertEqual(len(data["groups"][4]["items"]), 4)
        self.assertEqual(data["groups"][6]["category"], "무협 용어")
        self.assertEqual(self._log_count(pid, "naming_shop"), 1)
        self.assertTrue(self.calls)
        self.assertIn("[Tory Core Identity]", str(self.calls[-1]["system"]))
        self.assertIn("저작권", self.calls[-1]["prompt"])
        with self.assertRaises(ValueError):
            app._parse_naming_shop('{"groups": []}')

    def test_ui_has_five_diversion_cards(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-glump-diversion="character_tarot"', html)
        self.assertIn("캐릭터 타로 풀이", html)
        self.assertIn("캐릭터 퍼스널컬러 찾기", html)
        self.assertIn("내 캐릭터한테 어울리는 색, AI가 골라드려요", html)
        self.assertIn("glumpErColorCharacter", js)
        self.assertIn("내 캐릭터 운명, 타로로 한 번 봐드릴게요", html)
        self.assertEqual(html.count('data-glump-diversion="'), 6)
        self.assertIn('data-glump-diversion="naming_shop"', html)
        self.assertIn("작명소", html)
        self.assertIn("이름이 안 떠오를 때, 동양·서양·현대 한 번에 쭉 보기", html)
        self.assertIn("/api/glump/naming-shop", js)
        self.assertIn("naming_shop", js)
        self.assertIn("data-glump-name-copy", js)
        self.assertIn("/api/glump/character-tarot", js)
        self.assertIn("character_tarot", js)
        self.assertIn('data-glump-tool="brain_park"', html)
        self.assertIn("손가락 놀이터", html)
        self.assertIn("타이핑 말고 클릭만, 캐릭터랑 잠깐 놀아요", html)
        self.assertNotIn("부담없는 딴짓", html)
        self.assertNotIn("뇌 정지 놀이터", html)
        self.assertIn('id === "brain_park"', js)


if __name__ == "__main__":
    unittest.main()
