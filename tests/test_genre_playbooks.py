"""Genre playbook seed + worldscan A-group injection."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client

ROMANCE_SCENE = (
    "서연은 회귀한 뒤의 세 번째 이사회 아침을 맞았다. "
    "전생에서 이미 한 번 파산시켰던 계약서를 책상 위에 펼쳐 두고, "
    "상사 민혁이 문을 열기 전에 약점을 먼저 짚어 두기로 했다. "
    "민혁은 다른 사람 앞에서는 차갑게 굴었지만, 그녀에게만은 목소리를 낮췄다. "
    "\"이번엔 당신 방식대로 가죠.\" "
    "서연의 심장이 한 박자 늦게 뛰었다."
)

FANTASY_SCENE = (
    "산길을 걷던 묵연은 품에서 스마트폰을 꺼내 지도 앱으로 경로를 확인했다."
)

JS_LIKE_INDEXED = (
    "[프로젝트 누적 정보 - 참고용]\n"
    "등장인물: [\"서연\"]\n\n"
    "[현재 작업]\n"
    "아래 원고에서 이 작품의 세계관 또는 캐릭터 설정과 어긋나는 지점을 찾아내세요.\n\n"
    "[본문]\n"
    f"{ROMANCE_SCENE}\n\n"
    "[검사 결과]"
)

FORESHADOW_META = {
    "title": "전생 계약서",
    "target": "이사회",
    "buildup": [
        "서연이 전생에서 파산했던 계약서를 펼친다",
        "민혁은 남들 앞에선 차갑고 서연에게만 목소리를 낮춘다",
    ],
}

ANALYZE_MULTI_TEXT = (
    "### 1화\n"
    f"{ROMANCE_SCENE}\n\n"
    "### 2화\n"
    "비서 하준이 커피를 건네며 웃었다. 서연은 그의 친절이 너무 매끄러워 오히려 경계했다. "
    "민혁은 그 장면을 보고도 아무 말 없이 지나갔다.\n"
)

FANTASY_FORESHADOW = {
    "title": "스마트폰",
    "target": "산길",
    "buildup": ["묵연이 품에서 기기를 꺼낸다"],
}


class GenrePlaybookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=120)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_romance_modern_only(self) -> None:
        book = app.load_genre_playbook("romance", "modern")
        self.assertIsInstance(book, dict)
        self.assertIn("checklist", book)
        self.assertIn("A_judge", book["group_rules"])
        self.assertIn("B_suggest", book["group_rules"])
        self.assertIn("C_style", book["group_rules"])
        self.assertIsNone(app.load_genre_playbook("fantasy", "wuxia"))
        self.assertIsNone(app.load_genre_playbook("", "modern"))
        self.assertIsNotNone(app.load_genre_playbook("fantasy", "female"))
        self.assertIsNotNone(app.load_genre_playbook("fantasy", "male"))
        self.assertEqual(
            {key for key in app.load_genre_playbooks() if key != "deltas"},
            {"romance_modern", "romance_romfant", "fantasy_female", "fantasy_male"},
        )

    def test_worldscan_prompt_injects_only_for_romance_modern(self) -> None:
        baseline = app.SuperToryHandler._build_setting_break_scan_prompt("한 줄 원고")
        self.assertNotIn("[장르별 판단 기준]", baseline)
        self.assertIn("[현재 작업]", baseline)
        self.assertIn("[본문]", baseline)

        romance = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="modern"
        )
        self.assertIn("[장르별 판단 기준]", romance)
        self.assertIn("판타지 장치 오판 방지", romance)
        self.assertIn("오판 금지", romance)
        self.assertLess(romance.find("[장르별 판단 기준]"), romance.find("[본문]"))
        # 본문 앞쪽 기본 계약은 그대로.
        self.assertIn("[판단 근거 우선순위]", romance)
        self.assertIn("회빙환", romance)

        other = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="wuxia"
        )
        self.assertEqual(other, baseline)

    def test_a_group_builders_inject_only_for_romance_modern(self) -> None:
        builders = (
            lambda g, s: app.SuperToryHandler._build_focused_analysis_prompt(
                "한 줄 원고", main_genre=g, sub_genre=s
            ),
            lambda g, s: app.SuperToryHandler._build_focused_analysis_multi_prompt(
                "### 1화\n한 줄\n### 2화\n두 줄", main_genre=g, sub_genre=s
            ),
            lambda g, s: app.SuperToryHandler._build_tension_curve_prompt(
                "한 줄 원고", main_genre=g, sub_genre=s
            ),
            lambda g, s: app.SuperToryHandler._build_cliffhanger_score_prompt(
                "마지막 문단", main_genre=g, sub_genre=s
            ),
            lambda g, s: app.SuperToryHandler._build_ending_rewrite_prompt(
                "마지막 문단", "훅이 약함", main_genre=g, sub_genre=s
            ),
        )
        for builder in builders:
            baseline = builder("", "")
            self.assertNotIn("[장르별 판단 기준]", baseline)
            romance = builder("romance", "modern")
            self.assertIn("[장르별 판단 기준]", romance, msg=baseline[:80])
            self.assertIn("판타지 장치 오판 방지", romance)
            self.assertIn("오판 금지", romance)
            self.assertEqual(romance.count("[장르별 판단 기준]"), 1)
            self.assertEqual(builder("fantasy", "wuxia"), baseline)

    def test_a_group_dry_run_api(self) -> None:
        romance_cases = (
            {
                "mode": "foreshadow",
                "foreshadow": FORESHADOW_META,
                "scene_content": ROMANCE_SCENE,
            },
            {
                "mode": "plottwist",
                "foreshadow": FORESHADOW_META,
                "scene_content": ROMANCE_SCENE,
            },
            {
                "mode": "temphook",
                "temphook_kind": "curve",
                "scene_content": ROMANCE_SCENE,
            },
            {
                "mode": "analyze",
                "scene_content": ROMANCE_SCENE,
                "indexed_prompt": (
                    "[현재 작업]\n아래 회차를 분석하세요.\n\n"
                    f"[본문]\n{ROMANCE_SCENE}\n\n[분석 결과]"
                ),
            },
            {
                "mode": "analyze_multi",
                "scene_content": ANALYZE_MULTI_TEXT,
                "episode_count": 2,
            },
        )
        for extra in romance_cases:
            payload = {
                "dry_run": True,
                "project_title": "계약 연애",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "modern",
                "scene_title": "1화",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn("[장르별 판단 기준]", full, msg=extra["mode"])
            self.assertEqual(full.count("[장르별 판단 기준]"), 1, msg=extra["mode"])
            self.assertIn("오판 금지", full)

        fantasy_cases = (
            {
                "mode": "foreshadow",
                "foreshadow": FANTASY_FORESHADOW,
                "scene_content": FANTASY_SCENE,
            },
            {
                "mode": "plottwist",
                "foreshadow": FANTASY_FORESHADOW,
                "scene_content": FANTASY_SCENE,
            },
            {
                "mode": "temphook",
                "temphook_kind": "curve",
                "scene_content": FANTASY_SCENE,
                "task_prompt": app.SuperToryHandler._build_tension_curve_prompt(FANTASY_SCENE),
            },
            {
                "mode": "analyze",
                "scene_content": FANTASY_SCENE,
                "indexed_prompt": app.SuperToryHandler._build_focused_analysis_prompt(FANTASY_SCENE),
            },
        )
        for extra in fantasy_cases:
            payload = {
                "dry_run": True,
                "project_title": "검객",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "wuxia",
                "scene_title": "1화",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            self.assertNotIn("[장르별 판단 기준]", str(result.get("full_prompt") or ""), msg=extra["mode"])

    def test_dry_run_indexed_prompt_gets_server_inject(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worldscan",
                "dry_run": True,
                "project_title": "계약 연애",
                "main_genre": "romance",
                "sub_genre": "modern",
                "scene_content": ROMANCE_SCENE,
                "indexed_prompt": JS_LIKE_INDEXED,
            },
        )
        self.assertEqual(status, 200, result)
        full = str(result.get("full_prompt") or "")
        self.assertIn("[장르별 판단 기준]", full)
        self.assertEqual(full.count("[장르별 판단 기준]"), 1)
        self.assertIn("오판 금지", full)

        status, other = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worldscan",
                "dry_run": True,
                "project_title": "검객",
                "main_genre": "fantasy",
                "sub_genre": "wuxia",
                "scene_content": FANTASY_SCENE,
                "indexed_prompt": (
                    "[현재 작업]\n"
                    "아래 원고에서 이 작품의 세계관 또는 캐릭터 설정과 어긋나는 지점을 찾아내세요.\n\n"
                    "[본문]\n"
                    f"{FANTASY_SCENE}\n\n"
                    "[검사 결과]"
                ),
            },
        )
        self.assertEqual(status, 200, other)
        other_full = str(other.get("full_prompt") or "")
        self.assertNotIn("[장르별 판단 기준]", other_full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_romance_does_not_flag_regression_as_genre_break(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worldscan",
                "project_title": "계약 연애",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "modern",
                "main_genre_label": "로맨스",
                "sub_genre_label": "현대로맨스",
                "scene_title": "1화",
                "scene_content": ROMANCE_SCENE,
                "world_setting": (
                    "현대 서울의 재벌 그룹. 여주 서연은 회귀자다. "
                    "회귀는 이 작품의 의도된 장치이며 한 번만 일어났다. "
                    "오피스·이사회·계약 연애가 배경이다."
                ),
                "character_profiles": {
                    "서연": "회귀한 여주. 전생 기억을 갖고 있으며 계약서를 읽는 전문성이 있다.",
                    "민혁": "상사 남주. 남들 앞에선 차갑고 서연에게만 다른 얼굴을 보인다.",
                },
            },
        )
        self.assertEqual(status, 200, result)
        text = str(result.get("text") or "")
        self.assertTrue(text.strip())
        lowered = text.replace(" ", "")
        # 회귀 존재 자체를 장르 이탈/판타지 오류로 단정하면 실패.
        self.assertNotRegex(
            text,
            r"(판타지\s*(장르\s*)?(이탈|오류)|장르\s*이탈|이세계\s*장치|로맨스가\s*아닌)",
        )
        self.assertFalse(
            ("회귀" in text or "빙의" in text)
            and any(bad in lowered for bad in ("설정붕괴", "장르오류", "장르가아님", "판타지물이아님")),
            msg=text,
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_fantasy_prompt_has_no_playbook_section(self) -> None:
        indexed = app.SuperToryHandler._build_setting_break_scan_prompt(FANTASY_SCENE)
        status, dry = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worldscan",
                "dry_run": True,
                "project_title": "검객",
                "main_genre": "fantasy",
                "sub_genre": "wuxia",
                "scene_content": FANTASY_SCENE,
                "indexed_prompt": indexed,
            },
        )
        self.assertEqual(status, 200, dry)
        self.assertNotIn("[장르별 판단 기준]", str(dry.get("full_prompt") or ""))

        status, live = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worldscan",
                "project_title": "검객",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "wuxia",
                "scene_content": FANTASY_SCENE,
                "world_setting": "동양풍 무협. 칼과 내공만 존재한다. 스마트폰은 없다.",
                "character_profiles": {"묵연": "이 세계 토착 무사."},
                "indexed_prompt": indexed,
            },
        )
        self.assertEqual(status, 200, live)
        text = str(live.get("text") or "")
        self.assertTrue(text.strip())
        self.assertNotIn("[장르별 판단 기준]", text)

    def _assert_no_fantasy_device_misread(self, text: str) -> None:
        self.assertTrue(str(text or "").strip())
        lowered = str(text).replace(" ", "")
        self.assertNotRegex(
            text,
            r"(판타지\s*(장르\s*)?(이탈|오류)|장르\s*이탈|이세계\s*장치|로맨스가\s*아닌)",
        )
        self.assertFalse(
            ("회귀" in text or "빙의" in text)
            and any(bad in lowered for bad in ("설정붕괴", "장르오류", "장르가아님", "판타지물이아님")),
            msg=text,
        )

    def _romance_live_body(self, **extra) -> dict:
        return {
            "project_title": "계약 연애",
            "purpose": "web_novel",
            "main_genre": "romance",
            "sub_genre": "modern",
            "main_genre_label": "로맨스",
            "sub_genre_label": "현대로맨스",
            "scene_title": "1화",
            "scene_content": ROMANCE_SCENE,
            "world_setting": (
                "현대 서울의 재벌 그룹. 여주 서연은 회귀자다. "
                "회귀는 이 작품의 의도된 장치이며 한 번만 일어났다."
            ),
            "character_profiles": {
                "서연": "회귀한 여주. 계약서를 읽는 전문성이 있다.",
                "민혁": "상사 남주.",
                "하준": "비서. 서브남주 후보처럼 친절하다.",
            },
            **extra,
        }

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_a_group_romance_and_fantasy(self) -> None:
        romance_calls = (
            {"mode": "foreshadow", "foreshadow": FORESHADOW_META},
            {"mode": "plottwist", "foreshadow": FORESHADOW_META},
            {"mode": "temphook", "temphook_kind": "curve"},
            {"mode": "analyze"},
        )
        for extra in romance_calls:
            status, result = self.request(
                "POST", "/api/ai/assist", self._romance_live_body(**extra)
            )
            self.assertEqual(status, 200, (extra["mode"], result))
            self._assert_no_fantasy_device_misread(str(result.get("text") or ""))

        fantasy_calls = (
            {
                "mode": "foreshadow",
                "foreshadow": FANTASY_FORESHADOW,
                "scene_content": FANTASY_SCENE,
            },
            {
                "mode": "temphook",
                "temphook_kind": "curve",
                "scene_content": FANTASY_SCENE,
                "task_prompt": app.SuperToryHandler._build_tension_curve_prompt(FANTASY_SCENE),
            },
            {
                "mode": "analyze",
                "scene_content": FANTASY_SCENE,
                "indexed_prompt": app.SuperToryHandler._build_focused_analysis_prompt(FANTASY_SCENE),
            },
        )
        for extra in fantasy_calls:
            payload = {
                "dry_run": True,
                "project_title": "검객",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "wuxia",
                "scene_title": "1화",
                **extra,
            }
            status, dry = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, (extra["mode"], dry))
            self.assertNotIn("[장르별 판단 기준]", str(dry.get("full_prompt") or ""), msg=extra["mode"])


WEAK_HEROINE_SCENE = (
    "수아는 창가에 앉아 민재가 오기를 기다렸다. "
    "회사에서는 시키는 일만 했고, 의견을 묻는 자리에서는 고개만 숙였다. "
    "특별한 재주도, 배짱도, 전문성도 없었다. 민재가 커피를 사 오면 고개만 끄덕였다. "
    '"오늘 저녁은 네가 정해." 민재가 말했다. '
    '수아는 "아무거나"라고만 답했다. 질투도, 고백도, 스킨십도 없이 며칠이 그냥 지나갔다. '
    "민재가 다른 여자와 웃으며 엘리베이터를 타는 걸 보고도 수아는 아무 말도 하지 않았다."
)

WEAK_OUTLINE = (
    "현대 서울. 평범한 회사원 수아와 상사 민재가 만난다. "
    "수아는 특별한 강점 없이 민재의 결정을 따르며 관계가 천천히 이어진다. "
    "오해와 재회를 거쳐 결국 함께한다."
)


class GenrePlaybookBGroupTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=120)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _assert_b_section(self, text: str, body_needle: str, msg: str = "") -> None:
        full = str(text or "")
        self.assertIn("[장르별 판단 기준]", full, msg)
        self.assertEqual(full.count("[장르별 판단 기준]"), 1, msg)
        self.assertIn("감정의 쌓임", full, msg)
        self.assertIn("감정 진전 이벤트", full, msg)
        self.assertIn("여주 무기", full, msg)
        self.assertNotIn("오판 금지", full, msg)
        self.assertNotIn("판타지 장치 오판 방지", full, msg)
        if body_needle:
            self.assertLess(full.find("[장르별 판단 기준]"), full.find(body_needle), msg)

    def test_b_group_builders_inject_only_for_romance_modern(self) -> None:
        builders = (
            (
                lambda g, s: app.SuperToryHandler._build_next_idea_prompt(
                    "한 줄 원고", main_genre=g, sub_genre=s
                ),
                "[현재 회차 본문]",
            ),
            (
                lambda g, s: app.SuperToryHandler._build_brainstorm_prompt(
                    "한 줄 원고", "", main_genre=g, sub_genre=s
                ),
                "[현재 회차 또는 최근 원고]",
            ),
            (
                lambda g, s: app.SuperToryHandler._build_submission_synopsis_prompt(
                    "줄거리 개요", None, None, main_genre=g, sub_genre=s
                ),
                "[작가가 제공한 줄거리 개요 - 시작부터 결말까지]",
            ),
        )
        for builder, needle in builders:
            baseline = builder("", "")
            self.assertNotIn("[장르별 판단 기준]", baseline)
            romance = builder("romance", "modern")
            self._assert_b_section(romance, needle, msg=needle)
            self.assertEqual(builder("fantasy", "wuxia"), baseline)

        next_exists = app.SuperToryHandler._build_next_idea_with_next_scene_prompt(
            "직전 끝", "다음 시작", main_genre="romance", sub_genre="modern"
        )
        self._assert_b_section(next_exists, "[직전 회차 마지막 부분]")
        brainstorm_next = app.SuperToryHandler._build_brainstorm_with_next_scene_prompt(
            "현재", "다음", "", main_genre="romance", sub_genre="modern"
        )
        self._assert_b_section(brainstorm_next, "[현재 회차]")

        analyze_a = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="modern"
        )
        self.assertIn("오판 금지", analyze_a)
        suggest_only = app.format_genre_playbook_suggest_section("romance", "modern")
        self.assertIn("여주 무기", suggest_only)
        self.assertNotIn("오판 금지", suggest_only)

    def test_b_group_dry_run_api(self) -> None:
        romance_cases = (
            {
                "mode": "ideas",
                "scene_content": WEAK_HEROINE_SCENE,
                "body_needle": "[현재 회차 본문]",
            },
            {
                "mode": "brainstorm",
                "scene_content": WEAK_HEROINE_SCENE,
                "user_topic": "다음 감정선",
                "body_needle": "[현재 회차 또는 최근 원고]",
            },
            {
                "mode": "successfeedback",
                "scene_content": WEAK_HEROINE_SCENE,
                "focus_scene_only": True,
                "body_needle": "[본문]",
            },
            {
                "mode": "subsynopsis",
                "scene_content": "",
                "outline_summary": WEAK_OUTLINE,
                "body_needle": "[작가가 제공한 줄거리 개요 - 시작부터 결말까지]",
            },
        )
        for extra in romance_cases:
            needle = extra.pop("body_needle")
            payload = {
                "dry_run": True,
                "project_title": "아무거나",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "modern",
                "scene_title": "1화",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            self._assert_b_section(str(result.get("full_prompt") or ""), needle, msg=extra["mode"])

        fantasy_cases = (
            {"mode": "ideas", "scene_content": FANTASY_SCENE},
            {"mode": "brainstorm", "scene_content": FANTASY_SCENE, "user_topic": "다음 전개"},
            {"mode": "successfeedback", "scene_content": FANTASY_SCENE, "focus_scene_only": True},
            {
                "mode": "subsynopsis",
                "scene_content": "",
                "outline_summary": "무사가 검을 뽑아 마왕을 벤다.",
            },
        )
        for extra in fantasy_cases:
            payload = {
                "dry_run": True,
                "project_title": "검객",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "wuxia",
                "scene_title": "1화",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            self.assertNotIn(
                "[장르별 판단 기준]",
                str(result.get("full_prompt") or ""),
                msg=extra["mode"],
            )

    def test_b_group_indexed_prompt_gets_server_inject(self) -> None:
        indexed_ideas = (
            "[프로젝트 누적 정보 - 참고용]\n등장인물: [\"수아\"]\n\n"
            + app.SuperToryHandler._build_next_idea_prompt(WEAK_HEROINE_SCENE)
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "ideas",
                "dry_run": True,
                "project_title": "아무거나",
                "main_genre": "romance",
                "sub_genre": "modern",
                "scene_content": WEAK_HEROINE_SCENE,
                "indexed_prompt": indexed_ideas,
            },
        )
        self.assertEqual(status, 200, result)
        self._assert_b_section(str(result.get("full_prompt") or ""), "[현재 회차 본문]")

        indexed_sf = app.SuperToryHandler._build_focused_analysis_prompt(WEAK_HEROINE_SCENE)
        status, sf = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "successfeedback",
                "dry_run": True,
                "project_title": "아무거나",
                "main_genre": "romance",
                "sub_genre": "modern",
                "scene_content": WEAK_HEROINE_SCENE,
                "indexed_prompt": indexed_sf,
            },
        )
        self.assertEqual(status, 200, sf)
        self._assert_b_section(str(sf.get("full_prompt") or ""), "[본문]")

    def _romance_b_live_body(self, **extra) -> dict:
        return {
            "project_title": "아무거나",
            "purpose": "web_novel",
            "main_genre": "romance",
            "sub_genre": "modern",
            "main_genre_label": "로맨스",
            "sub_genre_label": "현대로맨스",
            "scene_title": "1화",
            "scene_content": WEAK_HEROINE_SCENE,
            "world_setting": "현대 서울 오피스. 판타지 장치 없음.",
            "character_profiles": {
                "수아": "여주. 특별한 강점·무기 없이 상대의 결정을 따르는 회사원.",
                "민재": "상사 남주.",
            },
            **extra,
        }

    def _assert_b_suggest_reflected(self, text: str, mode: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip(), msg=mode)
        lowered = body.replace(" ", "")
        hits = (
            "무기",
            "강점",
            "질투",
            "고백",
            "스킨십",
            "감정진전",
            "감정 진전",
            "밀당",
        )
        self.assertTrue(
            any(hit.replace(" ", "") in lowered or hit in body for hit in hits),
            msg=f"{mode}: B_suggest 반영이 안 보임\n{body}",
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_b_group_romance_and_fantasy(self) -> None:
        romance_calls = (
            {"mode": "ideas"},
            {"mode": "brainstorm", "user_topic": "여주 매력과 다음 감정선"},
            {"mode": "successfeedback", "focus_scene_only": True},
            {"mode": "subsynopsis", "scene_content": "", "outline_summary": WEAK_OUTLINE},
        )
        for extra in romance_calls:
            status, result = self.request(
                "POST", "/api/ai/assist", self._romance_b_live_body(**extra)
            )
            self.assertEqual(status, 200, (extra["mode"], result))
            self._assert_b_suggest_reflected(str(result.get("text") or ""), extra["mode"])

        fantasy_calls = (
            {"mode": "ideas", "scene_content": FANTASY_SCENE},
            {"mode": "brainstorm", "scene_content": FANTASY_SCENE, "user_topic": "다음 전개"},
            {"mode": "successfeedback", "scene_content": FANTASY_SCENE, "focus_scene_only": True},
            {
                "mode": "subsynopsis",
                "scene_content": "",
                "outline_summary": "무사가 검을 뽑아 마왕을 벤다.",
            },
        )
        for extra in fantasy_calls:
            payload = {
                "dry_run": True,
                "project_title": "검객",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "wuxia",
                "scene_title": "1화",
                **extra,
            }
            status, dry = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, (extra["mode"], dry))
            self.assertNotIn(
                "[장르별 판단 기준]",
                str(dry.get("full_prompt") or ""),
                msg=extra["mode"],
            )


C_ROMANCE_SCENE = (
    "서연은 계약서를 덮고 민혁을 바라봤다. "
    '"이번엔 당신 방식대로 가죠." '
    "민혁의 목소리가 낮아졌다. 서연은 대답 대신 창밖을 봤다."
)

C_FANTASY_SCENE = (
    "묵연은 검을 뽑아 산길을 가로막은 자를 노려봤다. "
    '"길을 비키시오." '
    "상대는 웃으며 창을 내밀었다. 묵연은 한 걸음 앞으로 나섰다."
)

C_ROMANCE_SELECTED = "민혁의 목소리가 낮아졌다. 서연은 대답 대신 창밖을 봤다."
C_FANTASY_SELECTED = "상대는 웃으며 창을 내밀었다. 묵연은 한 걸음 앞으로 나섰다."


class GenrePlaybookCGroupTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=120)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _assert_c_section(self, text: str, body_needle: str, msg: str = "") -> None:
        full = str(text or "")
        self.assertIn("[장르별 문체 기준]", full, msg)
        self.assertEqual(full.count("[장르별 문체 기준]"), 1, msg)
        self.assertIn("내면 독백", full, msg)
        self.assertIn("신체 반응", full, msg)
        self.assertIn("말줄임", full, msg)
        self.assertIn("강제하지 말", full, msg)
        self.assertIn("의도된 절제", full, msg)
        self.assertIn("의미 주체", full, msg)
        self.assertIn("구조적 수정이 아님", full, msg)
        self.assertIn("내면을 채워 넣지 말", full, msg)
        self.assertIn("다가오는 그의 기척", full, msg)
        self.assertIn("다듬지 않는 것을 권합니다", full, msg)
        self.assertNotIn("[장르별 판단 기준]", full, msg)
        self.assertNotIn("오판 금지", full, msg)
        if body_needle:
            self.assertLess(full.find("[장르별 문체 기준]"), full.find(body_needle), msg)

    def test_c_group_builders_inject_only_for_romance_modern(self) -> None:
        builders = (
            (
                lambda g, s: app.SuperToryHandler._build_continue_prompt(
                    "한 줄 원고", "short", "", "", main_genre=g, sub_genre=s
                ),
                "[원고]",
            ),
            (
                lambda g, s: app.SuperToryHandler._build_rewrite_prompt(
                    "한 줄", "", "", "", main_genre=g, sub_genre=s
                ),
                "[다듬을 문장]",
            ),
            (
                lambda g, s: app.SuperToryHandler._build_description_expand_prompt(
                    "한 줄", "", "", "", main_genre=g, sub_genre=s
                ),
                "[선택 원문]",
            ),
            (
                lambda g, s: app.SuperToryHandler._build_world_description_prompt(
                    "옥상", "한 줄 원고", main_genre=g, sub_genre=s
                ),
                "[현재 회차 - 문체 참고용]",
            ),
        )
        for builder, needle in builders:
            baseline = builder("", "")
            self.assertNotIn("[장르별 문체 기준]", baseline, msg=needle)
            romance = builder("romance", "modern")
            self._assert_c_section(romance, needle, msg=needle)
            self.assertEqual(builder("fantasy", "wuxia"), baseline, msg=needle)

        style_only = app.format_genre_playbook_style_section("romance", "modern")
        self.assertIn("내면 독백", style_only)
        self.assertIn("의도된 절제", style_only)
        self.assertIn("강제하지 말", style_only)
        self.assertIn("의미 주체", style_only)
        self.assertIn("구조적 수정이 아님", style_only)
        self.assertIn("내면을 채워 넣지 말", style_only)
        self.assertIn("다가오는 그의 기척", style_only)
        self.assertIn("다듬지 않는 것을 권합니다", style_only)
        self.assertNotIn("오판 금지", style_only)
        self.assertNotIn("여주 무기", style_only)

    def test_c_group_dry_run_api(self) -> None:
        romance_cases = (
            {
                "mode": "continue",
                "scene_content": C_ROMANCE_SCENE,
                "length_mode": "short",
                "body_needle": "[원고]",
            },
            {
                "mode": "rewrite",
                "scene_content": C_ROMANCE_SCENE,
                "selected_text": C_ROMANCE_SELECTED,
                "context_before": "서연은 계약서를 덮고 민혁을 바라봤다. ",
                "context_after": "",
                "body_needle": "[다듬을 문장]",
            },
            {
                "mode": "descexpand",
                "scene_content": C_ROMANCE_SCENE,
                "selected_text": C_ROMANCE_SELECTED,
                "body_needle": "[선택 원문]",
            },
            {
                "mode": "worlddesc",
                "scene_content": C_ROMANCE_SCENE,
                "target_subject": "비 오는 회사 옥상",
                "body_needle": "[현재 회차 - 문체 참고용]",
            },
        )
        for extra in romance_cases:
            needle = extra.pop("body_needle")
            payload = {
                "dry_run": True,
                "project_title": "계약 연애",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "modern",
                "scene_title": "1화",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            self._assert_c_section(str(result.get("full_prompt") or ""), needle, msg=extra["mode"])

        fantasy_cases = (
            {
                "mode": "continue",
                "scene_content": C_FANTASY_SCENE,
                "length_mode": "short",
            },
            {
                "mode": "rewrite",
                "scene_content": C_FANTASY_SCENE,
                "selected_text": C_FANTASY_SELECTED,
            },
            {
                "mode": "descexpand",
                "scene_content": C_FANTASY_SCENE,
                "selected_text": C_FANTASY_SELECTED,
            },
            {
                "mode": "worlddesc",
                "scene_content": C_FANTASY_SCENE,
                "target_subject": "산속 객잔 마당",
            },
        )
        for extra in fantasy_cases:
            payload = {
                "dry_run": True,
                "project_title": "검객",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "wuxia",
                "scene_title": "1화",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertNotIn("[장르별 문체 기준]", full, msg=extra["mode"])
            self.assertNotIn("[장르별 판단 기준]", full, msg=extra["mode"])

    def test_c_group_indexed_prompt_gets_server_inject(self) -> None:
        indexed = app.SuperToryHandler._build_continue_prompt(C_ROMANCE_SCENE, "short", "", "")
        self.assertNotIn("[장르별 문체 기준]", indexed)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "continue",
                "dry_run": True,
                "project_title": "계약 연애",
                "main_genre": "romance",
                "sub_genre": "modern",
                "scene_content": C_ROMANCE_SCENE,
                "length_mode": "short",
                "indexed_prompt": indexed,
            },
        )
        self.assertEqual(status, 200, result)
        self._assert_c_section(str(result.get("full_prompt") or ""), "[원고]")


ROMFANT_SCENE = (
    "엘레나는 세 번째 회귀에서 황제 카이엔의 손을 잡았다. "
    "전생에서 그는 그녀를 살리기 위해 북부 세 왕국을 불태웠고, "
    "살아남은 자들의 저주가 제국을 덮쳤다. "
    "그녀는 그 피로 물든 손을 놓지 않은 채 마계와 계약을 맺었다. "
    "영혼을 팔아 그를 다시 데려오기 위해서였다. "
    "카이엔이 눈을 떴을 때 엘레나는 피 묻은 왕관을 그의 머리에 올려 주었다. "
    '"이번엔 제가 당신을 구하겠어요."'
)

ROMFANT_CROSS_PHRASES = (
    "감정 강도·폭력성 오판 방지",
    "대량학살",
    "격식 있는 대사체",
    "여주의 주체적 선택",
)
MODERN_CROSS_PHRASES = (
    "판타지 장치 오판 방지",
    "여주 무기",
    "의도된 절제",
)


class GenrePlaybookRomfantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=120)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_romance_romfant(self) -> None:
        book = app.load_genre_playbook("romance", "romfant")
        self.assertIsInstance(book, dict)
        self.assertIn("감정 강도·폭력성 오판 방지", book["checklist"])
        self.assertIn("대량학살", book["group_rules"]["A_judge"])
        self.assertIn("여주의 주체적 선택", book["group_rules"]["B_suggest"])
        self.assertIn("격식 있는 대사체", book["group_rules"]["C_style"])
        modern = app.load_genre_playbook("romance", "modern")
        self.assertNotIn("대량학살", modern["checklist"])

    def test_romfant_builders_inject_abc_and_do_not_mix_with_modern(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="romfant"
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("대량학살", worldscan)
        self.assertNotIn("판타지 장치 오판 방지", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="romfant"
        )
        self.assertIn("[장르별 판단 기준]", ideas)
        self.assertIn("여주의 주체적 선택", ideas)
        self.assertNotIn("여주 무기", ideas)
        self.assertLess(ideas.find("[장르별 판단 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="romance", sub_genre="romfant"
        )
        self.assertIn("[장르별 문체 기준]", continue_p)
        self.assertIn("격식 있는 대사체", continue_p)
        self.assertNotIn("의도된 절제", continue_p)
        self.assertLess(continue_p.find("[장르별 문체 기준]"), continue_p.find("[원고]"))

        modern_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="modern"
        )
        modern_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="modern"
        )
        modern_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="romance", sub_genre="modern"
        )
        for phrase in ROMFANT_CROSS_PHRASES:
            self.assertNotIn(phrase, modern_scan, msg=phrase)
            self.assertNotIn(phrase, modern_ideas, msg=phrase)
            self.assertNotIn(phrase, modern_continue, msg=phrase)
        self.assertIn("판타지 장치 오판 방지", modern_scan)
        self.assertIn("여주 무기", modern_ideas)
        self.assertIn("의도된 절제", modern_continue)

    def test_romfant_dry_run_abc_api(self) -> None:
        cases = (
            {
                "mode": "worldscan",
                "scene_content": ROMFANT_SCENE,
                "heading": "[장르별 판단 기준]",
                "needle": "[본문]",
                "must": "대량학살",
                "must_not": "판타지 장치 오판 방지",
            },
            {
                "mode": "ideas",
                "scene_content": ROMFANT_SCENE,
                "heading": "[장르별 판단 기준]",
                "needle": "[현재 회차 본문]",
                "must": "여주의 주체적 선택",
                "must_not": "여주 무기",
            },
            {
                "mode": "continue",
                "scene_content": ROMFANT_SCENE,
                "length_mode": "short",
                "heading": "[장르별 문체 기준]",
                "needle": "[원고]",
                "must": "격식 있는 대사체",
                "must_not": "의도된 절제",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            needle = extra.pop("needle")
            must = extra.pop("must")
            must_not = extra.pop("must_not")
            payload = {
                "dry_run": True,
                "project_title": "악녀는 살아남기로 했다",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "romfant",
                "scene_title": "1화",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertIn(must, full, msg=extra["mode"])
            self.assertNotIn(must_not, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(needle), msg=extra["mode"])

            modern_payload = {
                **payload,
                "project_title": "계약 연애",
                "main_genre": "romance",
                "sub_genre": "modern",
            }
            status, modern = self.request("POST", "/api/ai/assist", modern_payload)
            self.assertEqual(status, 200, modern)
            modern_full = str(modern.get("full_prompt") or "")
            self.assertNotIn(must, modern_full, msg=extra["mode"])

    def _assert_no_extreme_scale_misread(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        lowered = body.replace(" ", "")
        self.assertNotRegex(
            body,
            r"(장르\s*이탈|윤리적\s*문제|비윤리|로판이\s*아닌|도를\s*넘)",
        )
        self.assertFalse(
            any(bad in lowered for bad in ("개연성오류", "개연성이없다", "과한폭력", "학살자체가문제")),
            msg=body,
        )

    def _romfant_live_body(self, **extra) -> dict:
        return {
            "project_title": "악녀는 살아남기로 했다",
            "purpose": "web_novel",
            "main_genre": "romance",
            "sub_genre": "romfant",
            "main_genre_label": "로맨스",
            "sub_genre_label": "로판",
            "scene_title": "1화",
            "scene_content": ROMFANT_SCENE,
            "world_setting": (
                "서양풍 판타지 제국. 마법과 신분제가 실재한다. "
                "여주 엘레나는 회귀자다. 남주 카이엔은 황제다. "
                "사랑을 위해 왕국을 불태우거나 마계와 계약하는 초월적 행위가 "
                "이 작품의 장르 문법이다."
            ),
            "character_profiles": {
                "엘레나": "회귀한 여주. 전생의 실패를 알고 스스로 운명을 바꾸려 한다.",
                "카이엔": "황제. 엘레나를 살리기 위해 극단적 선택을 한다.",
            },
            **extra,
        }

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_romfant_does_not_flag_extreme_scale(self) -> None:
        for extra in (
            {"mode": "worldscan"},
            {"mode": "analyze"},
        ):
            status, result = self.request(
                "POST", "/api/ai/assist", self._romfant_live_body(**extra)
            )
            self.assertEqual(status, 200, (extra["mode"], result))
            self._assert_no_extreme_scale_misread(str(result.get("text") or ""))


FANTASY_FEMALE_SCENE = (
    "리나는 사막 균열 앞에서 지팡이를 꽉 쥐었다. "
    '"저, 저건… 정화해야…" 말이 입안에서 뭉개졌다. '
    "동료들은 이미 뒤로 물러나 있었다. "
    "그녀는 혼자 모래 위에 정화의 원을 그렸다. "
    "손이 떨렸지만 주문의 결은 흔들리지 않았다. "
    "균열이 비명을 지르며 봉합됐다. "
    "아무도 그녀를 구하러 오지 않았다. 올 필요도 없었다."
)

FANTASY_FEMALE_CROSS_PHRASES = (
    "걸크러쉬답지 않다",
    "위기 해결의 주체",
    "여주 스스로 해결하는 방식",
    "액션/전략 중심",
)


class GenrePlaybookFantasyFemaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=120)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_fantasy_female(self) -> None:
        book = app.load_genre_playbook("fantasy", "female")
        self.assertIsInstance(book, dict)
        self.assertIn("위기 해결의 주체", book["checklist"])
        self.assertIn("걸크러쉬답지 않다", book["group_rules"]["A_judge"])
        self.assertIn("여주 스스로 해결하는 방식", book["group_rules"]["B_suggest"])
        self.assertIn("액션/전략 중심", book["group_rules"]["C_style"])
        romfant = app.load_genre_playbook("romance", "romfant")
        self.assertNotIn("걸크러쉬답지 않다", romfant["checklist"])
        self.assertNotIn("걸크러쉬답지 않다", romfant["group_rules"]["A_judge"])

    def test_fantasy_female_builders_inject_abc_and_do_not_mix_with_romfant(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="female"
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("걸크러쉬답지 않다", worldscan)
        self.assertIn("위기 해결의 주체", worldscan)
        self.assertNotIn("대량학살", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="female"
        )
        self.assertIn("[장르별 판단 기준]", ideas)
        self.assertIn("여주 스스로 해결하는 방식", ideas)
        self.assertNotIn("여주의 주체적 선택", ideas)
        self.assertLess(ideas.find("[장르별 판단 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="fantasy", sub_genre="female"
        )
        self.assertIn("[장르별 문체 기준]", continue_p)
        self.assertIn("액션/전략 중심", continue_p)
        self.assertNotIn("격식 있는 대사체", continue_p)
        self.assertLess(continue_p.find("[장르별 문체 기준]"), continue_p.find("[원고]"))

        romfant_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="romfant"
        )
        romfant_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="romfant"
        )
        romfant_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="romance", sub_genre="romfant"
        )
        for phrase in FANTASY_FEMALE_CROSS_PHRASES:
            self.assertNotIn(phrase, romfant_scan, msg=phrase)
            self.assertNotIn(phrase, romfant_ideas, msg=phrase)
            self.assertNotIn(phrase, romfant_continue, msg=phrase)
        self.assertIn("대량학살", romfant_scan)
        self.assertIn("여주의 주체적 선택", romfant_ideas)
        self.assertIn("격식 있는 대사체", romfant_continue)

    def test_fantasy_female_dry_run_abc_api(self) -> None:
        cases = (
            {
                "mode": "worldscan",
                "scene_content": FANTASY_FEMALE_SCENE,
                "heading": "[장르별 판단 기준]",
                "needle": "[본문]",
                "must": "걸크러쉬답지 않다",
                "must_not": "대량학살",
            },
            {
                "mode": "ideas",
                "scene_content": FANTASY_FEMALE_SCENE,
                "heading": "[장르별 판단 기준]",
                "needle": "[현재 회차 본문]",
                "must": "여주 스스로 해결하는 방식",
                "must_not": "여주의 주체적 선택",
            },
            {
                "mode": "continue",
                "scene_content": FANTASY_FEMALE_SCENE,
                "length_mode": "short",
                "heading": "[장르별 문체 기준]",
                "needle": "[원고]",
                "must": "액션/전략 중심",
                "must_not": "격식 있는 대사체",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            needle = extra.pop("needle")
            must = extra.pop("must")
            must_not = extra.pop("must_not")
            payload = {
                "dry_run": True,
                "project_title": "사막의 정화사",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "female",
                "scene_title": "1화",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertIn(must, full, msg=extra["mode"])
            self.assertNotIn(must_not, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(needle), msg=extra["mode"])

            romfant_payload = {
                **payload,
                "project_title": "악녀는 살아남기로 했다",
                "main_genre": "romance",
                "sub_genre": "romfant",
            }
            status, romfant = self.request("POST", "/api/ai/assist", romfant_payload)
            self.assertEqual(status, 200, romfant)
            romfant_full = str(romfant.get("full_prompt") or "")
            self.assertNotIn(must, romfant_full, msg=extra["mode"])
            for phrase in FANTASY_FEMALE_CROSS_PHRASES:
                self.assertNotIn(phrase, romfant_full, msg=f"{extra['mode']}:{phrase}")

    def _assert_no_girlcrush_personality_misread(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        lowered = body.replace(" ", "")
        self.assertNotRegex(body, r"걸크러쉬\s*(답지\s*않|가\s*아니)")
        self.assertNotRegex(
            body,
            r"소극적이(라서|어서|니)\s*(안\s*된다|문제|결함|장르)",
        )
        self.assertNotRegex(
            body,
            r"당당하지\s*않(아서|으니|다).{0,16}(장르|결함|문제)",
        )
        self.assertFalse(
            any(
                bad in lowered
                for bad in (
                    "걸크러쉬가아니다",
                    "걸크러쉬답지않다",
                    "여주가너무소극적",
                    "성격이소극적이라문제",
                )
            ),
            msg=body,
        )

    def _fantasy_female_live_body(self, **extra) -> dict:
        return {
            "project_title": "사막의 정화사",
            "purpose": "web_novel",
            "main_genre": "fantasy",
            "sub_genre": "female",
            "main_genre_label": "판타지",
            "sub_genre_label": "여성향판타지",
            "scene_title": "1화",
            "scene_content": FANTASY_FEMALE_SCENE,
            "world_setting": (
                "서양풍 판타지. 정화 마법은 원을 그리고 대가를 치르면 발동한다. "
                "리나는 대륙 최강급 정화 마법사다. 수줍고 말을 더듬지만 "
                "위기는 본인의 마법으로 혼자 해결한다. 로맨스는 없다."
            ),
            "character_profiles": {
                "리나": (
                    "수줍고 말더듬는 여주. 성격은 소극적이지만 "
                    "압도적 정화 마법으로 균열을 혼자 봉합한다."
                ),
            },
            **extra,
        }

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_fantasy_female_does_not_flag_shy_heroine(self) -> None:
        for extra in (
            {"mode": "worldscan"},
            {"mode": "analyze"},
        ):
            status, result = self.request(
                "POST", "/api/ai/assist", self._fantasy_female_live_body(**extra)
            )
            self.assertEqual(status, 200, (extra["mode"], result))
            self._assert_no_girlcrush_personality_misread(str(result.get("text") or ""))


FANTASY_MALE_MASSACRE_SCENE = (
    "강현은 홍련멸진의 발동 조건을 채웠다. 3서클 화염, 검기 오의, 시스템창 스킬 레벨 47. "
    "배신한 남부 연합 삼만은 협곡에 갇혔다. "
    "그가 손을 내리자 익힌 범위 안에서만 화염이 쏟아졌다. "
    "살아남은 지휘관을 단숨에 베고, 군기는 불탔다. "
    "시체가 길을 메웠다. 강현은 창을 확인했다. 마나 43퍼센트. 예상 소모량과 같았다."
)

FANTASY_MALE_CHEAT_SCENE = (
    "강현은 전생에서 3년 뒤 북부 던전 붕괴를 직접 겪었다. "
    "이번 생에는 그 날짜가 오기 전에 입구 함정의 위치를 지도에 옮기고, "
    "방패 상단에 물자를 선주문했다. 시스템창에 미래 예고는 없다. 전생 기억뿐이다. "
    "길드가 입찰을 시작하기도 전에 그는 이미 대피 경로를 그렸다."
)

FANTASY_MALE_TERRITORY_SCENE = (
    "강현은 어제 황무지 영지를 받았다. 주민은 열두 명, 밀 창고는 비어 있었고 세금 장부도 없었다. "
    "투자한 자원도, 데려온 인력도 없었다. "
    "다음 날 아침 돌 성벽이 완성되고 기사단 오백이 충성 맹세를 했으며 금광이 터졌다. "
    "주민 만족도는 하루 만에 최상으로 찍혔다."
)

FANTASY_MALE_CROSS_PHRASES = (
    "폭력성/전투 스케일 오판 방지",
    "치트 유형별 논리",
    "사이다 구조(위기→응징)",
    "전투 장면은 타격감 있게",
    "이를 악물고 창을 고쳐 잡았다",
    "경영 논리",
)


class GenrePlaybookFantasyMaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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

    def test_load_fantasy_male(self) -> None:
        book = app.load_genre_playbook("fantasy", "male")
        self.assertIsInstance(book, dict)
        self.assertIn("폭력성/전투 스케일 오판 방지", book["checklist"])
        self.assertIn("치트 유형별 논리", book["group_rules"]["A_judge"])
        self.assertIn("사이다 구조(위기→응징)", book["group_rules"]["B_suggest"])
        self.assertIn("전투 장면은 타격감 있게", book["group_rules"]["C_style"])
        self.assertIn("이를 악물고 창을 고쳐 잡았다", book["group_rules"]["C_style"])
        self.assertIn("나쁜 다듬기(피할 것)", book["group_rules"]["C_style"])
        female = app.load_genre_playbook("fantasy", "female")
        self.assertNotIn("폭력성/전투 스케일 오판 방지", female["checklist"])
        self.assertNotIn("치트 유형별 논리", female["group_rules"]["A_judge"])

    def test_fantasy_male_builders_inject_abc_and_do_not_mix_with_female(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("폭력성/전투 스케일 오판 방지", worldscan)
        self.assertIn("치트 유형별 논리", worldscan)
        self.assertNotIn("걸크러쉬답지 않다", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        self.assertIn("[장르별 판단 기준]", ideas)
        self.assertIn("사이다 구조(위기→응징)", ideas)
        self.assertNotIn("여주 스스로 해결하는 방식", ideas)
        self.assertLess(ideas.find("[장르별 판단 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="fantasy", sub_genre="male"
        )
        self.assertIn("[장르별 문체 기준]", continue_p)
        self.assertIn("전투 장면은 타격감 있게", continue_p)
        self.assertIn("이를 악물고 창을 고쳐 잡았다", continue_p)
        self.assertNotIn("액션/전략 중심", continue_p)
        self.assertLess(continue_p.find("[장르별 문체 기준]"), continue_p.find("[원고]"))

        female_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="female"
        )
        female_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="female"
        )
        female_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="fantasy", sub_genre="female"
        )
        for phrase in FANTASY_MALE_CROSS_PHRASES:
            self.assertNotIn(phrase, female_scan, msg=phrase)
            self.assertNotIn(phrase, female_ideas, msg=phrase)
            self.assertNotIn(phrase, female_continue, msg=phrase)
        self.assertIn("걸크러쉬답지 않다", female_scan)
        self.assertIn("여주 스스로 해결하는 방식", female_ideas)
        self.assertIn("액션/전략 중심", female_continue)

    def test_fantasy_male_dry_run_abc_api(self) -> None:
        cases = (
            {
                "mode": "worldscan",
                "scene_content": FANTASY_MALE_MASSACRE_SCENE,
                "heading": "[장르별 판단 기준]",
                "needle": "[본문]",
                "must": "폭력성/전투 스케일 오판 방지",
                "must_not": "걸크러쉬답지 않다",
            },
            {
                "mode": "ideas",
                "scene_content": FANTASY_MALE_MASSACRE_SCENE,
                "heading": "[장르별 판단 기준]",
                "needle": "[현재 회차 본문]",
                "must": "사이다 구조(위기→응징)",
                "must_not": "여주 스스로 해결하는 방식",
            },
            {
                "mode": "continue",
                "scene_content": FANTASY_MALE_MASSACRE_SCENE,
                "length_mode": "short",
                "heading": "[장르별 문체 기준]",
                "needle": "[원고]",
                "must": "전투 장면은 타격감 있게",
                "must_not": "액션/전략 중심",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            needle = extra.pop("needle")
            must = extra.pop("must")
            must_not = extra.pop("must_not")
            payload = {
                "dry_run": True,
                "project_title": "회귀한 영주",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "scene_title": "1화",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertIn(must, full, msg=extra["mode"])
            self.assertNotIn(must_not, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(needle), msg=extra["mode"])

            female_payload = {
                **payload,
                "project_title": "사막의 정화사",
                "main_genre": "fantasy",
                "sub_genre": "female",
            }
            status, female = self.request("POST", "/api/ai/assist", female_payload)
            self.assertEqual(status, 200, female)
            female_full = str(female.get("full_prompt") or "")
            self.assertNotIn(must, female_full, msg=extra["mode"])
            for phrase in FANTASY_MALE_CROSS_PHRASES:
                self.assertNotIn(phrase, female_full, msg=f"{extra['mode']}:{phrase}")

    def _assert_no_combat_ethics_misread(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        lowered = body.replace(" ", "")
        self.assertNotRegex(
            body,
            r"(윤리적\s*문제|비윤리|도를\s*넘|장르\s*이탈|폭력이\s*(문제|과도))",
        )
        self.assertFalse(
            any(
                bad in lowered
                for bad in (
                    "학살자체가문제",
                    "살상이문제",
                    "과도한폭력",
                    "비윤리적",
                    "폭력성이문제",
                    "폭력은안된다",
                )
            ),
            msg=body,
        )

    def _assert_experience_cheat_logic(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        hits = ("회귀", "전생", "미래", "정보", "경험", "개연", "치트", "기억")
        self.assertTrue(any(hit in body for hit in hits), msg=body)
        self.assertNotRegex(body, r"(장르\s*이탈|회귀가\s*문제|판타지\s*오류)")

    def _assert_territory_leap_flagged(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        self.assertNotIn("어긋나는 지점이 발견되지 않았습니다", body)
        hits = ("급성장", "자원", "경영", "비약", "투자", "인력", "개연", "하루", "없이", "성벽")
        self.assertTrue(any(hit in body for hit in hits), msg=body)

    def _fantasy_male_live_body(self, scene_content: str, **extra) -> dict:
        return {
            "project_title": "회귀한 영주",
            "purpose": "web_novel",
            "main_genre": "fantasy",
            "sub_genre": "male",
            "main_genre_label": "판타지",
            "sub_genre_label": "남성향판타지",
            "scene_title": "1화",
            "scene_content": scene_content,
            "world_setting": (
                "서양풍 판타지. 시스템창과 스킬이 실재한다. 강현은 회귀자다. "
                "화염 스킬 홍련멸진은 3서클·마나 소모로 발동하며 익힌 범위 안에서만 쓴다. "
                "영지 경영은 자원-투자-성과가 비례한다."
            ),
            "character_profiles": {
                "강현": (
                    "회귀한 주인공. 홍련멸진을 이미 익혔고, "
                    "전생 기억으로 위기를 미리 피한다. 영지를 경영한다."
                ),
            },
            **extra,
        }

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_fantasy_male_does_not_flag_combat_scale(self) -> None:
        for extra in (
            {"mode": "worldscan"},
            {"mode": "analyze"},
        ):
            status, result = self.request(
                "POST",
                "/api/ai/assist",
                self._fantasy_male_live_body(FANTASY_MALE_MASSACRE_SCENE, **extra),
            )
            self.assertEqual(status, 200, (extra["mode"], result))
            self._assert_no_combat_ethics_misread(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_fantasy_male_flags_experience_cheat_logic(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._fantasy_male_live_body(FANTASY_MALE_CHEAT_SCENE, mode="analyze"),
        )
        self.assertEqual(status, 200, result)
        self._assert_experience_cheat_logic(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_fantasy_male_flags_territory_leap(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._fantasy_male_live_body(FANTASY_MALE_TERRITORY_SCENE, mode="worldscan"),
        )
        self.assertEqual(status, 200, result)
        self._assert_territory_leap_flagged(str(result.get("text") or ""))


PLAYBOOK_REGRESSION_GENRES = (
    ("romance", "modern"),
    ("romance", "romfant"),
    ("fantasy", "female"),
    ("fantasy", "male"),
)


class GenrePlaybookDeltaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        app._GENRE_PLAYBOOKS_CACHE = None
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=120)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _dry_run(self, main: str, sub: str, mode: str, genre_detail: str = "") -> str:
        extra: dict = {"length_mode": "short"} if mode == "continue" else {}
        payload = {
            "dry_run": True,
            "mode": mode,
            "project_title": "델타 회귀",
            "purpose": "web_novel",
            "main_genre": main,
            "sub_genre": sub,
            "scene_title": "1화",
            "scene_content": "한 줄 원고",
            **extra,
        }
        if genre_detail:
            payload["genre_detail"] = genre_detail
        status, result = self.request("POST", "/api/ai/assist", payload)
        self.assertEqual(status, 200, result)
        return str(result.get("full_prompt") or "")

    def test_json_has_no_delta_content(self) -> None:
        books = app.load_genre_playbooks()
        for key, book in books.items():
            if key == "deltas":
                continue
            self.assertNotIn("_delta", book, msg=key)

    def test_missing_delta_keeps_base_byte_identical(self) -> None:
        formatters = (
            app.format_genre_playbook_judge_section,
            app.format_genre_playbook_suggest_section,
            app.format_genre_playbook_style_section,
        )
        for main, sub in PLAYBOOK_REGRESSION_GENRES:
            for fmt in formatters:
                base = fmt(main, sub)
                self.assertTrue(base, msg=f"{main}/{sub}")
                self.assertEqual(fmt(main, sub, ""), base)
                self.assertEqual(fmt(main, sub, None), base)
                self.assertEqual(fmt(main, sub, "isekai"), base)
                if (main, sub) == ("romance", "modern"):
                    historical = fmt(main, sub, "historical")
                    self.assertNotEqual(historical, base)
                    self.assertIn("[세부장르 추가", historical)
                    self.assertEqual(fmt(main, sub, "oriental_romfant"), base)
                    self.assertEqual(fmt(main, sub, "alt_history"), base)
                    self.assertEqual(fmt(main, sub, "murim"), base)
                    self.assertEqual(fmt(main, sub, "urban"), base)
                    self.assertEqual(fmt(main, sub, "hidden_world"), base)
                    self.assertEqual(fmt(main, sub, "traditional"), base)
                    self.assertEqual(fmt(main, sub, "sports"), base)
                elif (main, sub) == ("romance", "romfant"):
                    oriental = fmt(main, sub, "oriental_romfant")
                    self.assertNotEqual(oriental, base)
                    self.assertIn("[세부장르 추가", oriental)
                    self.assertEqual(fmt(main, sub, "historical"), base)
                    self.assertEqual(fmt(main, sub, "alt_history"), base)
                    self.assertEqual(fmt(main, sub, "murim"), base)
                    self.assertEqual(fmt(main, sub, "urban"), base)
                    self.assertEqual(fmt(main, sub, "hidden_world"), base)
                    self.assertEqual(fmt(main, sub, "traditional"), base)
                    self.assertEqual(fmt(main, sub, "sports"), base)
                elif (main, sub) == ("fantasy", "male"):
                    alt = fmt(main, sub, "alt_history")
                    self.assertNotEqual(alt, base)
                    self.assertIn("[세부장르 추가", alt)
                    murim = fmt(main, sub, "murim")
                    self.assertNotEqual(murim, base)
                    self.assertIn("[세부장르 추가", murim)
                    urban = fmt(main, sub, "urban")
                    self.assertNotEqual(urban, base)
                    self.assertIn("[세부장르 추가", urban)
                    hidden = fmt(main, sub, "hidden_world")
                    self.assertNotEqual(hidden, base)
                    self.assertIn("[세부장르 추가", hidden)
                    traditional = fmt(main, sub, "traditional")
                    self.assertNotEqual(traditional, base)
                    self.assertIn("[세부장르 추가", traditional)
                    sports = fmt(main, sub, "sports")
                    self.assertNotEqual(sports, base)
                    self.assertIn("[세부장르 추가", sports)
                    self.assertNotEqual(urban, hidden)
                    self.assertNotEqual(urban, traditional)
                    self.assertNotEqual(sports, urban)
                    self.assertNotEqual(sports, hidden)
                    self.assertEqual(fmt(main, sub, "historical"), base)
                    self.assertEqual(fmt(main, sub, "oriental_romfant"), base)
                else:
                    self.assertEqual(fmt(main, sub, "historical"), base)
                    self.assertEqual(fmt(main, sub, "oriental_romfant"), base)
                    self.assertEqual(fmt(main, sub, "alt_history"), base)
                    self.assertEqual(fmt(main, sub, "murim"), base)
                    self.assertEqual(fmt(main, sub, "urban"), base)
                    self.assertEqual(fmt(main, sub, "hidden_world"), base)
                    self.assertEqual(fmt(main, sub, "traditional"), base)
                    self.assertEqual(fmt(main, sub, "sports"), base)
                self.assertNotIn("[세부장르 추가", base)

    def test_regression_dry_run_without_genre_detail_matches_existing_structure(self) -> None:
        modes = (
            ("worldscan", "[장르별 판단 기준]", "[세부장르 추가 기준]", "[세부장르 추가 문체 기준]"),
            ("ideas", "[장르별 판단 기준]", "[세부장르 추가 기준]", "[세부장르 추가 문체 기준]"),
            ("continue", "[장르별 문체 기준]", "[세부장르 추가 문체 기준]", "[세부장르 추가 기준]"),
        )
        for main, sub in PLAYBOOK_REGRESSION_GENRES:
            for mode, heading, extra_a, extra_b in modes:
                full = self._dry_run(main, sub, mode)
                with_empty = self._dry_run(main, sub, mode, "")
                self.assertEqual(full, with_empty, msg=f"{main}/{sub}/{mode}")
                self.assertIn(heading, full, msg=f"{main}/{sub}/{mode}")
                self.assertEqual(full.count(heading), 1, msg=f"{main}/{sub}/{mode}")
                self.assertNotIn(extra_a, full, msg=f"{main}/{sub}/{mode}")
                self.assertNotIn(extra_b, full, msg=f"{main}/{sub}/{mode}")

    def test_genre_detail_without_json_delta_is_base_only_via_api(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "여성향 대체역사 델타 없음",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "female",
                "genre_detail": "alt_history",
            },
        )
        self.assertEqual(status, 201, project)
        self.assertEqual(project.get("genre_detail"), "")
        cases = (
            ("worldscan", "[장르별 판단 기준]"),
            ("ideas", "[장르별 판단 기준]"),
            ("continue", "[장르별 문체 기준]"),
        )
        for mode, heading in cases:
            extra = {"length_mode": "short"} if mode == "continue" else {}
            status, result = self.request(
                "POST",
                "/api/ai/assist",
                {
                    "dry_run": True,
                    "mode": mode,
                    "project_id": project["id"],
                    "project_title": "여성향 대체역사 델타 없음",
                    "purpose": "web_novel",
                    "main_genre": "fantasy",
                    "sub_genre": "female",
                    "genre_detail": "alt_history",
                    "scene_title": "1화",
                    "scene_content": "한 줄 원고",
                    **extra,
                },
            )
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=mode)
            self.assertEqual(full.count(heading), 1, msg=mode)
            self.assertNotIn("[세부장르 추가", full, msg=mode)
            self.assertNotIn("TEST_DELTA_", full, msg=mode)

    def _install_test_delta(self) -> None:
        books = app.load_genre_playbooks()
        deltas = books.setdefault("deltas", {})
        if not isinstance(deltas, dict):
            deltas = {}
            books["deltas"] = deltas
        deltas["romance_modern__historical"] = {
            "checklist_addition": "TEST_DELTA_CHECKLIST_TOKEN",
            "reader_expectations_addition": "TEST_DELTA_EXPECT_TOKEN",
            "tone_addition": "TEST_DELTA_TONE_TOKEN",
            "group_rules_addition": {
                "A_judge": "TEST_DELTA_A_JUDGE_TOKEN",
                "B_suggest": "TEST_DELTA_B_SUGGEST_TOKEN",
                "C_style": "TEST_DELTA_C_STYLE_TOKEN",
            },
        }

    def test_injected_delta_inserts_second_block_for_abc(self) -> None:
        self._install_test_delta()
        judge = app.format_genre_playbook_judge_section("romance", "modern", "historical")
        self.assertIn("[장르별 판단 기준]", judge)
        self.assertIn("[세부장르 추가 기준]", judge)
        self.assertIn("TEST_DELTA_CHECKLIST_TOKEN", judge)
        self.assertIn("TEST_DELTA_A_JUDGE_TOKEN", judge)
        self.assertLess(judge.find("[장르별 판단 기준]"), judge.find("[세부장르 추가 기준]"))
        self.assertEqual(judge.count("[장르별 판단 기준]"), 1)
        self.assertEqual(judge.count("[세부장르 추가 기준]"), 1)
        base_judge = app.format_genre_playbook_judge_section("romance", "modern")
        self.assertEqual(judge[: len(base_judge)], base_judge)
        self.assertNotIn("TEST_DELTA_CHECKLIST_TOKEN", base_judge)

        suggest = app.format_genre_playbook_suggest_section("romance", "modern", "historical")
        self.assertIn("[장르별 판단 기준]", suggest)
        self.assertIn("[세부장르 추가 기준]", suggest)
        self.assertIn("TEST_DELTA_EXPECT_TOKEN", suggest)
        self.assertIn("TEST_DELTA_B_SUGGEST_TOKEN", suggest)
        self.assertNotIn("TEST_DELTA_A_JUDGE_TOKEN", suggest)
        self.assertLess(suggest.find("[장르별 판단 기준]"), suggest.find("[세부장르 추가 기준]"))

        style = app.format_genre_playbook_style_section("romance", "modern", "historical")
        self.assertIn("[장르별 문체 기준]", style)
        self.assertIn("[세부장르 추가 문체 기준]", style)
        self.assertIn("TEST_DELTA_TONE_TOKEN", style)
        self.assertIn("TEST_DELTA_C_STYLE_TOKEN", style)
        self.assertNotIn("[세부장르 추가 기준]", style)
        self.assertLess(style.find("[장르별 문체 기준]"), style.find("[세부장르 추가 문체 기준]"))

        prompt = "[현재 작업]\n검사하세요.\n\n[본문]\n한 줄\n"
        injected = app.inject_genre_playbook_judge_section(
            prompt, "romance", "modern", "historical"
        )
        self.assertLess(injected.find("[장르별 판단 기준]"), injected.find("[세부장르 추가 기준]"))
        self.assertLess(injected.find("[세부장르 추가 기준]"), injected.find("[본문]"))
        self.assertEqual(
            app.inject_genre_playbook_judge_section(injected, "romance", "modern", "historical"),
            injected,
        )

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="modern", genre_detail="historical"
        )
        self.assertIn("TEST_DELTA_B_SUGGEST_TOKEN", ideas)
        self.assertLess(ideas.find("[세부장르 추가 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고",
            "short",
            "",
            "",
            main_genre="romance",
            sub_genre="modern",
            genre_detail="historical",
        )
        self.assertIn("TEST_DELTA_C_STYLE_TOKEN", continue_p)
        self.assertLess(continue_p.find("[세부장르 추가 문체 기준]"), continue_p.find("[원고]"))

    def test_injected_delta_dry_run_api_inserts_second_block(self) -> None:
        self._install_test_delta()
        cases = (
            {
                "mode": "worldscan",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "token": "TEST_DELTA_A_JUDGE_TOKEN",
                "needle": "[본문]",
            },
            {
                "mode": "ideas",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "token": "TEST_DELTA_B_SUGGEST_TOKEN",
                "needle": "[현재 회차 본문]",
            },
            {
                "mode": "continue",
                "heading": "[장르별 문체 기준]",
                "extra": "[세부장르 추가 문체 기준]",
                "token": "TEST_DELTA_C_STYLE_TOKEN",
                "needle": "[원고]",
                "length_mode": "short",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            extra_heading = extra.pop("extra")
            token = extra.pop("token")
            needle = extra.pop("needle")
            payload = {
                "dry_run": True,
                "project_title": "계약 연애",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "modern",
                "genre_detail": "historical",
                "scene_title": "1화",
                "scene_content": "한 줄 원고",
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertIn(extra_heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertEqual(full.count(extra_heading), 1, msg=extra["mode"])
            self.assertIn(token, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(extra_heading), msg=extra["mode"])
            self.assertLess(full.find(extra_heading), full.find(needle), msg=extra["mode"])

            without = self._dry_run("romance", "modern", extra["mode"])
            self.assertNotIn(extra_heading, without, msg=extra["mode"])
            self.assertNotIn(token, without, msg=extra["mode"])


HISTORICAL_COMMONER_SCENE = (
    "장터 골목에서 연이는 보리떡을 접시에 담았다. "
    '"아저씨, 떡 세 개요." '
    "포목전 점원 길동이 손을 내밀며 웃었다. "
    '"오늘 밀가루값 올랐다니까. 내일은 더 비쌀지도." '
    "연이는 소매로 손을 닦으며 고개를 끄덕였다. "
    '"그럼 내일 새벽에 다시 올게."'
)

HISTORICAL_SPEECH_ERROR_SCENE = (
    "장터에서 떡을 팔던 연이가 길동에게 허리를 굽혔다. "
    '"전하, 소인이 아뢰옵니다. 오늘 밀가루가 떨어졌사옵니다." '
    "길동은 포목전 점원이었다. "
    "그는 마마께 올리듯 대답했다. "
    '"그리 알고 있지. 과인이 내일 다시 들르마."'
)

HISTORICAL_CROSS_PHRASES = (
    "궁중/반가/평민",
    "신분/시대적 제약",
    "궁중체를 서민 서사에 기본값",
    "저잣거리·반가·평민",
)


class GenrePlaybookHistoricalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        app._GENRE_PLAYBOOKS_CACHE = None
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_romance_modern_historical_delta(self) -> None:
        delta = app.load_genre_playbook_delta("romance", "modern", "historical")
        self.assertIsInstance(delta, dict)
        self.assertIn("저잣거리", delta["identity_addition"])
        self.assertIn("궁중/반가/평민", delta["group_rules_addition"]["A_judge"])
        self.assertIn("신분/시대적 제약", delta["group_rules_addition"]["B_suggest"])
        self.assertIn("궁중체를 서민 서사에 기본값", delta["group_rules_addition"]["C_style"])
        self.assertIsNone(app.load_genre_playbook_delta("romance", "modern", ""))
        self.assertIsNone(app.load_genre_playbook_delta("romance", "romfant", "historical"))

    def test_historical_builders_inject_delta_and_plain_modern_does_not(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="modern", genre_detail="historical"
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("[세부장르 추가 기준]", worldscan)
        self.assertIn("궁중/반가/평민", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[세부장르 추가 기준]"))
        self.assertLess(worldscan.find("[세부장르 추가 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="modern", genre_detail="historical"
        )
        self.assertIn("[세부장르 추가 기준]", ideas)
        self.assertIn("신분/시대적 제약", ideas)
        self.assertLess(ideas.find("[세부장르 추가 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고",
            "short",
            "",
            "",
            main_genre="romance",
            sub_genre="modern",
            genre_detail="historical",
        )
        self.assertIn("[세부장르 추가 문체 기준]", continue_p)
        self.assertIn("궁중체를 서민 서사에 기본값", continue_p)
        self.assertLess(continue_p.find("[세부장르 추가 문체 기준]"), continue_p.find("[원고]"))

        plain_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="modern"
        )
        plain_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="modern"
        )
        plain_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="romance", sub_genre="modern"
        )
        for phrase in HISTORICAL_CROSS_PHRASES:
            self.assertNotIn(phrase, plain_scan, msg=phrase)
            self.assertNotIn(phrase, plain_ideas, msg=phrase)
            self.assertNotIn(phrase, plain_continue, msg=phrase)
        self.assertNotIn("[세부장르 추가 기준]", plain_scan)
        self.assertNotIn("[세부장르 추가 기준]", plain_ideas)
        self.assertNotIn("[세부장르 추가 문체 기준]", plain_continue)

    def test_historical_dry_run_abc_api(self) -> None:
        cases = (
            {
                "mode": "worldscan",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "궁중/반가/평민",
                "needle": "[본문]",
            },
            {
                "mode": "ideas",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "신분/시대적 제약",
                "needle": "[현재 회차 본문]",
            },
            {
                "mode": "continue",
                "heading": "[장르별 문체 기준]",
                "extra": "[세부장르 추가 문체 기준]",
                "must": "궁중체를 서민 서사에 기본값",
                "needle": "[원고]",
                "length_mode": "short",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            extra_heading = extra.pop("extra")
            must = extra.pop("must")
            needle = extra.pop("needle")
            payload = {
                "dry_run": True,
                "project_title": "세부장르 UI 검증 사극 2",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "modern",
                "genre_detail": "historical",
                "scene_title": "1화",
                "scene_content": HISTORICAL_COMMONER_SCENE,
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertIn(extra_heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertEqual(full.count(extra_heading), 1, msg=extra["mode"])
            self.assertIn(must, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(extra_heading), msg=extra["mode"])
            self.assertLess(full.find(extra_heading), full.find(needle), msg=extra["mode"])

            plain = {
                **payload,
                "project_title": "계약 연애",
                "genre_detail": "",
            }
            status, modern = self.request("POST", "/api/ai/assist", plain)
            self.assertEqual(status, 200, modern)
            modern_full = str(modern.get("full_prompt") or "")
            self.assertNotIn(extra_heading, modern_full, msg=extra["mode"])
            self.assertNotIn(must, modern_full, msg=extra["mode"])

    def _historical_live_body(self, scene_content: str, **extra) -> dict:
        return {
            "project_title": "세부장르 UI 검증 사극 2",
            "purpose": "web_novel",
            "main_genre": "romance",
            "sub_genre": "modern",
            "genre_detail": "historical",
            "main_genre_label": "로맨스",
            "sub_genre_label": "현대로맨스",
            "genre_detail_label": "사극",
            "scene_title": "1화",
            "scene_content": scene_content,
            "world_setting": (
                "조선풍 가상 마을. 궁궐이 아니라 저잣거리가 무대다. "
                "연이와 길동은 평민이다. 궁중 어투는 쓰지 않는다."
            ),
            "character_profiles": {
                "연이": "떡을 파는 평민 여주.",
                "길동": "포목전 점원. 평민 남주.",
            },
            **extra,
        }

    def _assert_no_palace_default_misread(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        self.assertNotRegex(
            body,
            r"(사극인데\s*궁중|궁중이\s*안\s*나|궁중\s*어투를\s*(써야|쓰라|강요)|전하를\s*써야)",
        )
        lowered = body.replace(" ", "")
        self.assertFalse(
            any(
                bad in lowered
                for bad in (
                    "궁중이없어서문제",
                    "궁중어투가없다",
                    "사극이아닌",
                    "평민서사는안됨",
                )
            ),
            msg=body,
        )

    def _assert_speech_register_flagged(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        self.assertNotIn("어긋나는 지점이 발견되지 않았습니다", body)
        hits = ("전하", "소인", "과인", "궁중", "신분", "어투", "호칭", "평민")
        self.assertTrue(any(hit in body for hit in hits), msg=body)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_historical_accepts_commoner_setting(self) -> None:
        for extra in (
            {"mode": "worldscan"},
            {"mode": "analyze"},
        ):
            status, result = self.request(
                "POST",
                "/api/ai/assist",
                self._historical_live_body(HISTORICAL_COMMONER_SCENE, **extra),
            )
            self.assertEqual(status, 200, (extra["mode"], result))
            self._assert_no_palace_default_misread(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_historical_flags_palace_speech_on_commoner(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._historical_live_body(HISTORICAL_SPEECH_ERROR_SCENE, mode="worldscan"),
        )
        self.assertEqual(status, 200, result)
        self._assert_speech_register_flagged(str(result.get("text") or ""))


ORIENTAL_FOX_SCENE = (
    "설화는 구미호 저주를 받은 지 일곱 해째였다. "
    "보름달이 뜨면 여우 귀가 돋고, 그 대가로 다음날 내공이 반나절 막혔다. "
    "규칙대로 오늘도 달이 차오르자 귀가 섰다. "
    "그녀는 청운문 연무장에 나가지 않고 별원에 숨었다. "
    "운혁은 초절정 고수였지만, 저주의 대가를 대신 져 주지는 않았다."
)

ORIENTAL_WESTERN_TITLE_SCENE = (
    "운혁 대공은 청운문 장문인의 공자였다. "
    "설화가 포권으로 예를 갖추자 그는 손을 내밀어 말했다. "
    '"각하께서 이번 비무의 심판을 맡으셨소. 공작 전하의 명이오." '
    "설화는 영애처럼 치마폭을 쥐었다."
)

ORIENTAL_CROSS_PHRASES = (
    "내공/경지",
    "강호체 어투",
    "서양풍 호칭이 섞이지 않도록",
    "문파 간 갈등이나 강호 정세",
)


class GenrePlaybookOrientalRomfantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        app._GENRE_PLAYBOOKS_CACHE = None
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_oriental_romfant_delta(self) -> None:
        delta = app.load_genre_playbook_delta("romance", "romfant", "oriental_romfant")
        self.assertIsInstance(delta, dict)
        self.assertIn("무협적 형태소", delta["identity_addition"])
        self.assertIn("서양풍 호칭·구조가 섞여 있으면", delta["group_rules_addition"]["A_judge"])
        self.assertIn("문파 간 갈등이나 강호 정세", delta["group_rules_addition"]["B_suggest"])
        self.assertIn("서양풍 호칭이 섞이지 않도록", delta["group_rules_addition"]["C_style"])
        self.assertIsNone(app.load_genre_playbook_delta("romance", "romfant", ""))
        self.assertIsNone(app.load_genre_playbook_delta("romance", "modern", "oriental_romfant"))

    def test_oriental_builders_inject_delta_and_plain_romfant_does_not(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고",
            main_genre="romance",
            sub_genre="romfant",
            genre_detail="oriental_romfant",
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("[세부장르 추가 기준]", worldscan)
        self.assertIn("내공/경지", worldscan)
        self.assertIn("대량학살", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[세부장르 추가 기준]"))
        self.assertLess(worldscan.find("[세부장르 추가 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고",
            main_genre="romance",
            sub_genre="romfant",
            genre_detail="oriental_romfant",
        )
        self.assertIn("[세부장르 추가 기준]", ideas)
        self.assertIn("문파 간 갈등이나 강호 정세", ideas)
        self.assertLess(ideas.find("[세부장르 추가 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고",
            "short",
            "",
            "",
            main_genre="romance",
            sub_genre="romfant",
            genre_detail="oriental_romfant",
        )
        self.assertIn("[세부장르 추가 문체 기준]", continue_p)
        self.assertIn("강호체 어투", continue_p)
        self.assertIn("격식 있는 대사체", continue_p)
        self.assertLess(continue_p.find("[세부장르 추가 문체 기준]"), continue_p.find("[원고]"))

        plain_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="romfant"
        )
        plain_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="romance", sub_genre="romfant"
        )
        plain_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="romance", sub_genre="romfant"
        )
        for phrase in ORIENTAL_CROSS_PHRASES:
            self.assertNotIn(phrase, plain_scan, msg=phrase)
            self.assertNotIn(phrase, plain_ideas, msg=phrase)
            self.assertNotIn(phrase, plain_continue, msg=phrase)
        self.assertNotIn("[세부장르 추가 기준]", plain_scan)
        self.assertNotIn("[세부장르 추가 기준]", plain_ideas)
        self.assertNotIn("[세부장르 추가 문체 기준]", plain_continue)
        self.assertIn("대량학살", plain_scan)
        self.assertIn("여주의 주체적 선택", plain_ideas)
        self.assertIn("격식 있는 대사체", plain_continue)

    def test_oriental_dry_run_abc_api(self) -> None:
        cases = (
            {
                "mode": "worldscan",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "내공/경지",
                "needle": "[본문]",
            },
            {
                "mode": "ideas",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "문파 간 갈등이나 강호 정세",
                "needle": "[현재 회차 본문]",
            },
            {
                "mode": "continue",
                "heading": "[장르별 문체 기준]",
                "extra": "[세부장르 추가 문체 기준]",
                "must": "강호체 어투",
                "needle": "[원고]",
                "length_mode": "short",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            extra_heading = extra.pop("extra")
            must = extra.pop("must")
            needle = extra.pop("needle")
            payload = {
                "dry_run": True,
                "project_title": "청운문의 소저",
                "purpose": "web_novel",
                "main_genre": "romance",
                "sub_genre": "romfant",
                "genre_detail": "oriental_romfant",
                "scene_title": "1화",
                "scene_content": ORIENTAL_FOX_SCENE,
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertIn(extra_heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertEqual(full.count(extra_heading), 1, msg=extra["mode"])
            self.assertIn(must, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(extra_heading), msg=extra["mode"])
            self.assertLess(full.find(extra_heading), full.find(needle), msg=extra["mode"])

            plain = {
                **payload,
                "project_title": "악녀는 살아남기로 했다",
                "genre_detail": "",
            }
            status, romfant = self.request("POST", "/api/ai/assist", plain)
            self.assertEqual(status, 200, romfant)
            romfant_full = str(romfant.get("full_prompt") or "")
            self.assertNotIn(extra_heading, romfant_full, msg=extra["mode"])
            self.assertNotIn(must, romfant_full, msg=extra["mode"])

    def _oriental_live_body(self, scene_content: str, **extra) -> dict:
        return {
            "project_title": "청운문의 소저",
            "purpose": "web_novel",
            "main_genre": "romance",
            "sub_genre": "romfant",
            "genre_detail": "oriental_romfant",
            "main_genre_label": "로맨스",
            "sub_genre_label": "로판",
            "genre_detail_label": "동양로판",
            "scene_title": "1화",
            "scene_content": scene_content,
            "world_setting": (
                "동양 무협풍 강호. 문파와 내공·경지가 실재한다. "
                "구미호 저주는 보름달에 여우 귀가 돋고 다음날 내공이 반나절 막히는 규칙이다. "
                "이 저주는 세계관 근간이며 장르 이탈이 아니다. "
                "호칭은 소저·공자·대협을 쓰고, 공작·대공·각하 같은 서양 귀족 호칭은 없다."
            ),
            "character_profiles": {
                "설화": "청운문 소저. 구미호 저주를 받은 여주. 회귀자다.",
                "운혁": "초절정 고수. 설화와 정혼 관계에 가깝다.",
            },
            **extra,
        }

    def _assert_no_supernatural_existence_misread(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        self.assertNotRegex(
            body,
            r"(장르\s*이탈|로판이\s*아닌|수인(이|은)\s*(문제|오류)|구미호.{0,16}(안\s*됨|오류|이탈))",
        )
        lowered = body.replace(" ", "")
        self.assertFalse(
            any(
                bad in lowered
                for bad in (
                    "초자연요소가문제",
                    "요괴설정이오류",
                    "수인은안됨",
                    "저주가장르이탈",
                    "구미호는안됨",
                )
            ),
            msg=body,
        )

    def _assert_western_title_flagged(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        self.assertNotIn("어긋나는 지점이 발견되지 않았습니다", body)
        hits = ("공작", "대공", "각하", "영애", "서양", "호칭")
        self.assertTrue(any(hit in body for hit in hits), msg=body)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_oriental_does_not_flag_fox_curse_existence(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._oriental_live_body(ORIENTAL_FOX_SCENE, mode="worldscan"),
        )
        self.assertEqual(status, 200, result)
        self._assert_no_supernatural_existence_misread(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_oriental_flags_western_titles(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._oriental_live_body(ORIENTAL_WESTERN_TITLE_SCENE, mode="worldscan"),
        )
        self.assertEqual(status, 200, result)
        self._assert_western_title_flagged(str(result.get("text") or ""))


ALT_HISTORY_DIVERGENCE_SCENE = (
    "이순신은 이번 세계에서 한 번도 칼을 잡지 않은 개성 상인이었다. "
    "임진년에도 그는 수군이 아니라 명나라 비단 시세를 계산하고 있었다. "
    "조정은 그를 충무공으로 부르지 않았다. 그건 이 작품의 분기점 자체였다. "
    "그는 왜군 보급로를 은으로 끊어, 한산 앞바다를 싸우지 않고 막았다."
)

ALT_HISTORY_NATION_SCENE = (
    "조선의 수군이 쓰시마를 넘어 왜의 보급항을 닫자, 한양의 창고에 쌀이 쌓이기 시작했다. "
    "왕은 북방 기병을 재편하고 요동 교역로를 열었다. "
    "십 년 안에 조선은 동아시아의 해상 강국이 되어 조공 대신 대등한 조약을 맺었다. "
    "신립은 자원을 계산하며 다음 항구를 고를 뿐이었다. 하루아침에 대포가 생긴 것은 아니었다."
)

ALT_HISTORY_PURE_SCENE = (
    "현감 박치원은 이 시대에서 태어난 조선 후기 관리였다. 스마트폰도, 전생의 기억도 없었다. "
    "홍경래의 난이 일어나기 전날, 그는 세금 장부를 덮고 민심이 아니라 군량을 먼저 풀기로 했다. "
    "그 선택으로 난은 사흘 만에 잦아들었고, 평안도의 창고는 비었지만 성은 남았다. "
    "다음 해 봄, 조정은 그를 탐관으로 의심했다. 군량을 먼저 푼 인과는 그렇게 이어졌다."
)

ALT_HISTORY_CROSS_PHRASES = (
    "의도된 개변",
    "다음 역사적 분기점이나 실존인물",
    "정치/전략적 서술과 사이다식 응징",
    "순수 대체역사",
)


class GenrePlaybookAltHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        app._GENRE_PLAYBOOKS_CACHE = None
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_alt_history_delta(self) -> None:
        delta = app.load_genre_playbook_delta("fantasy", "male", "alt_history")
        self.assertIsInstance(delta, dict)
        self.assertIn("역사적 사실을 if로 재구성", delta["identity_addition"])
        self.assertIn("의도된 개변 여부 구분", delta["group_rules_addition"]["A_judge"])
        self.assertIn("다음 역사적 분기점이나 실존인물", delta["group_rules_addition"]["B_suggest"])
        self.assertIn("정치/전략적 서술과 사이다식 응징", delta["group_rules_addition"]["C_style"])
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "male", ""))
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "female", "alt_history"))
        self.assertIsNone(app.load_genre_playbook_delta("romance", "modern", "alt_history"))

    def test_alt_history_builders_inject_delta_and_plain_male_does_not(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="alt_history",
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("[세부장르 추가 기준]", worldscan)
        self.assertIn("의도된 개변", worldscan)
        self.assertIn("폭력성/전투 스케일 오판 방지", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[세부장르 추가 기준]"))
        self.assertLess(worldscan.find("[세부장르 추가 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="alt_history",
        )
        self.assertIn("[세부장르 추가 기준]", ideas)
        self.assertIn("다음 역사적 분기점이나 실존인물", ideas)
        self.assertIn("사이다 구조(위기→응징)", ideas)
        self.assertLess(ideas.find("[세부장르 추가 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고",
            "short",
            "",
            "",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="alt_history",
        )
        self.assertIn("[세부장르 추가 문체 기준]", continue_p)
        self.assertIn("정치/전략적 서술과 사이다식 응징", continue_p)
        self.assertIn("전투 장면은 타격감 있게", continue_p)
        self.assertLess(continue_p.find("[세부장르 추가 문체 기준]"), continue_p.find("[원고]"))

        analyze = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="alt_history",
        )
        self.assertIn("[장르별 판단 기준]", analyze)
        self.assertIn("[세부장르 추가 기준]", analyze)
        self.assertIn("정치적으로 재단하지 말고", analyze)
        self.assertLess(analyze.find("[세부장르 추가 기준]"), analyze.find("[본문]"))

        plain_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="fantasy", sub_genre="male"
        )
        plain_analyze = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        for phrase in ALT_HISTORY_CROSS_PHRASES:
            self.assertNotIn(phrase, plain_scan, msg=phrase)
            self.assertNotIn(phrase, plain_ideas, msg=phrase)
            self.assertNotIn(phrase, plain_continue, msg=phrase)
            self.assertNotIn(phrase, plain_analyze, msg=phrase)
        self.assertNotIn("[세부장르 추가 기준]", plain_scan)
        self.assertNotIn("[세부장르 추가 기준]", plain_ideas)
        self.assertNotIn("[세부장르 추가 문체 기준]", plain_continue)
        self.assertNotIn("[세부장르 추가 기준]", plain_analyze)
        self.assertIn("폭력성/전투 스케일 오판 방지", plain_scan)
        self.assertIn("사이다 구조(위기→응징)", plain_ideas)
        self.assertIn("전투 장면은 타격감 있게", plain_continue)

    def test_alt_history_dry_run_abc_api(self) -> None:
        cases = (
            {
                "mode": "worldscan",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "의도된 개변",
                "needle": "[본문]",
            },
            {
                "mode": "ideas",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "다음 역사적 분기점이나 실존인물",
                "needle": "[현재 회차 본문]",
            },
            {
                "mode": "continue",
                "heading": "[장르별 문체 기준]",
                "extra": "[세부장르 추가 문체 기준]",
                "must": "정치/전략적 서술과 사이다식 응징",
                "needle": "[원고]",
                "length_mode": "short",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            extra_heading = extra.pop("extra")
            must = extra.pop("must")
            needle = extra.pop("needle")
            payload = {
                "dry_run": True,
                "project_title": "임진년의 상인",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "alt_history",
                "scene_title": "1화",
                "scene_content": ALT_HISTORY_DIVERGENCE_SCENE,
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertIn(extra_heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertEqual(full.count(extra_heading), 1, msg=extra["mode"])
            self.assertIn(must, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(extra_heading), msg=extra["mode"])
            self.assertLess(full.find(extra_heading), full.find(needle), msg=extra["mode"])

            plain = {
                **payload,
                "project_title": "회귀한 영주",
                "genre_detail": "",
            }
            status, male = self.request("POST", "/api/ai/assist", plain)
            self.assertEqual(status, 200, male)
            male_full = str(male.get("full_prompt") or "")
            self.assertNotIn(extra_heading, male_full, msg=extra["mode"])
            self.assertNotIn(must, male_full, msg=extra["mode"])

    def _alt_history_live_body(self, scene_content: str, **extra) -> dict:
        return {
            "project_title": "임진년의 상인",
            "purpose": "web_novel",
            "main_genre": "fantasy",
            "sub_genre": "male",
            "genre_detail": "alt_history",
            "main_genre_label": "판타지",
            "sub_genre_label": "남성향판타지",
            "genre_detail_label": "대체역사",
            "scene_title": "1화",
            "scene_content": scene_content,
            "world_setting": (
                "대체역사. 핵심 설정은 이순신이 무인이 아니라 개성 상인이라는 의도된 개변이다. "
                "원 역사의 충무공 행적을 그대로 쓰지 않는 것은 고증 오류가 아니라 장르 장치다. "
                "조선이 해상 강국으로 일어서는 국가 부흥 서사는 이 장르의 표준이며 정치적 편향이 아니다. "
                "현대인 회귀·빙의·지식 치트는 필수가 아니다. "
                "박치원처럼 그 시대 인물만으로 성립하는 순수 대체역사도 유효하다."
            ),
            "character_profiles": {
                "이순신": "이 세계의 개성 상인. 수군 장수가 아니다. 의도된 개변.",
                "박치원": "조선 후기 현감. 현대인 개입 없는 그 시대 인물.",
                "신립": "자원을 계산하는 무장. 국가 부흥 서사의 실무자.",
            },
            **extra,
        }

    def _assert_no_intended_divergence_misread(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        self.assertNotRegex(
            body,
            r"(고증\s*오류|역사\s*왜곡|실존인물.{0,12}오류|원\s*역사와\s*달라.{0,16}문제)",
        )
        lowered = body.replace(" ", "")
        self.assertFalse(
            any(
                bad in lowered
                for bad in (
                    "고증오류",
                    "역사왜곡",
                    "이순신이틀렸다",
                    "상인설정이오류",
                    "의도된개변이문제",
                )
            ),
            msg=body,
        )

    def _assert_no_political_bias_read(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        self.assertNotRegex(
            body,
            r"(정치적\s*(편향|문제|선동)|국뽕.{0,8}(문제|오류|지적)|민족주의.{0,8}(문제|위험)|프로파간다)",
        )
        lowered = body.replace(" ", "")
        self.assertFalse(
            any(
                bad in lowered
                for bad in (
                    "정치적편향",
                    "정치적으로문제",
                    "국뽕이문제",
                    "국뽕은안됨",
                    "편향된서술",
                )
            ),
            msg=body,
        )

    def _assert_no_knowledge_cheat_misread(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        self.assertNotRegex(
            body,
            r"(현대\s*지식.{0,12}(사용|적용|치트)|지식\s*치트.{0,12}(오류|문제|위반)|회귀자|빙의|전생\s*기억|시스템창)",
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_alt_history_does_not_flag_intended_divergence(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._alt_history_live_body(ALT_HISTORY_DIVERGENCE_SCENE, mode="worldscan"),
        )
        self.assertEqual(status, 200, result)
        self._assert_no_intended_divergence_misread(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_alt_history_does_not_flag_nation_rise_as_bias(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._alt_history_live_body(ALT_HISTORY_NATION_SCENE, mode="analyze"),
        )
        self.assertEqual(status, 200, result)
        self._assert_no_political_bias_read(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_alt_history_pure_does_not_apply_knowledge_cheat(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._alt_history_live_body(ALT_HISTORY_PURE_SCENE, mode="worldscan"),
        )
        self.assertEqual(status, 200, result)
        self._assert_no_knowledge_cheat_misread(str(result.get("text") or ""))


MURIM_MODERN_SPEECH_SCENE = (
    "청운검문 삼류무사 진운이 장로 앞에 무릎을 꿇었다. "
    "\"고맙습니다, 도와주셔서. 진짜 살 것 같아요.\" "
    "맞은편 사매가 검집을 만지며 속삭였다. "
    "\"그 사람 되게 강하다. 우리 그냥 도망가자.\" "
    "진운은 물병을 들이키고 고개를 끄덕였다. "
    "\"알겠어. 다음에 보자.\""
)

MURIM_HYPOCRISY_SCENE = (
    "화산파 장로 청송은 정파의 대의와 인의를 입에 올렸다. "
    "그날 밤 그는 사파 포로 열두 명의 단전을 뽑고, 시체를 계곡에 버렸다. "
    "문중 제자들에게는 '사파의 잔당을 정화했다'고만 알렸다. "
    "진운은 그 광경을 보고도, 이건 실수가 아니라 화산파가 오래 써 온 방식임을 알고 있었다. "
    "장문인은 이를 알고도 눈을 감았다. 명분은 인의요, 실리는 단전이었다."
)

MURIM_CROSS_PHRASES = (
    "정파의 이중잣대가 의도적 설정인지",
    "다음 경지 돌파나 은원 해소",
    "뼈에 새기겠소",
    "강호체 어투와 관용구",
)


class GenrePlaybookMurimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        app._GENRE_PLAYBOOKS_CACHE = None
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_murim_delta(self) -> None:
        delta = app.load_genre_playbook_delta("fantasy", "male", "murim")
        self.assertIsInstance(delta, dict)
        self.assertIn("중국풍 무협 세계관", delta["identity_addition"])
        self.assertIn("정파의 이중잣대가 의도적 설정인지", delta["group_rules_addition"]["A_judge"])
        self.assertIn("다음 경지 돌파나 은원 해소", delta["group_rules_addition"]["B_suggest"])
        self.assertIn("강호체 어투와 관용구", delta["group_rules_addition"]["C_style"])
        alt = app.load_genre_playbook_delta("fantasy", "male", "alt_history")
        self.assertIsInstance(alt, dict)
        self.assertNotEqual(delta["identity_addition"], alt["identity_addition"])
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "male", ""))
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "female", "murim"))
        self.assertIsNone(app.load_genre_playbook_delta("romance", "romfant", "murim"))
        self.assertIsNone(app.load_genre_playbook_delta("romance", "modern", "murim"))

    def test_murim_builders_inject_delta_and_plain_male_does_not(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="murim",
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("[세부장르 추가 기준]", worldscan)
        self.assertIn("정파의 이중잣대가 의도적 설정인지", worldscan)
        self.assertIn("폭력성/전투 스케일 오판 방지", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[세부장르 추가 기준]"))
        self.assertLess(worldscan.find("[세부장르 추가 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="murim",
        )
        self.assertIn("[세부장르 추가 기준]", ideas)
        self.assertIn("다음 경지 돌파나 은원 해소", ideas)
        self.assertIn("사이다 구조(위기→응징)", ideas)
        self.assertLess(ideas.find("[세부장르 추가 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고",
            "short",
            "",
            "",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="murim",
        )
        self.assertIn("[세부장르 추가 문체 기준]", continue_p)
        self.assertIn("뼈에 새기겠소", continue_p)
        self.assertIn("전투 장면은 타격감 있게", continue_p)
        self.assertLess(continue_p.find("[세부장르 추가 문체 기준]"), continue_p.find("[원고]"))

        analyze = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="murim",
        )
        self.assertIn("[장르별 판단 기준]", analyze)
        self.assertIn("[세부장르 추가 기준]", analyze)
        self.assertIn("정파의 이중잣대가 의도적 설정인지", analyze)
        self.assertLess(analyze.find("[세부장르 추가 기준]"), analyze.find("[본문]"))

        plain_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="fantasy", sub_genre="male"
        )
        plain_analyze = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        for phrase in MURIM_CROSS_PHRASES:
            self.assertNotIn(phrase, plain_scan, msg=phrase)
            self.assertNotIn(phrase, plain_ideas, msg=phrase)
            self.assertNotIn(phrase, plain_continue, msg=phrase)
            self.assertNotIn(phrase, plain_analyze, msg=phrase)
        self.assertNotIn("[세부장르 추가 기준]", plain_scan)
        self.assertNotIn("[세부장르 추가 기준]", plain_ideas)
        self.assertNotIn("[세부장르 추가 문체 기준]", plain_continue)
        self.assertNotIn("[세부장르 추가 기준]", plain_analyze)
        self.assertIn("폭력성/전투 스케일 오판 방지", plain_scan)
        self.assertIn("사이다 구조(위기→응징)", plain_ideas)
        self.assertIn("전투 장면은 타격감 있게", plain_continue)

    def test_murim_dry_run_abc_api(self) -> None:
        cases = (
            {
                "mode": "worldscan",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "정파의 이중잣대가 의도적 설정인지",
                "needle": "[본문]",
            },
            {
                "mode": "ideas",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "다음 경지 돌파나 은원 해소",
                "needle": "[현재 회차 본문]",
            },
            {
                "mode": "continue",
                "heading": "[장르별 문체 기준]",
                "extra": "[세부장르 추가 문체 기준]",
                "must": "뼈에 새기겠소",
                "needle": "[원고]",
                "length_mode": "short",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            extra_heading = extra.pop("extra")
            must = extra.pop("must")
            needle = extra.pop("needle")
            payload = {
                "dry_run": True,
                "project_title": "청운검문의 진운",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "murim",
                "scene_title": "1화",
                "scene_content": MURIM_MODERN_SPEECH_SCENE,
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertIn(extra_heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertEqual(full.count(extra_heading), 1, msg=extra["mode"])
            self.assertIn(must, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(extra_heading), msg=extra["mode"])
            self.assertLess(full.find(extra_heading), full.find(needle), msg=extra["mode"])

            plain = {
                **payload,
                "project_title": "회귀한 영주",
                "genre_detail": "",
            }
            status, male = self.request("POST", "/api/ai/assist", plain)
            self.assertEqual(status, 200, male)
            male_full = str(male.get("full_prompt") or "")
            self.assertNotIn(extra_heading, male_full, msg=extra["mode"])
            self.assertNotIn(must, male_full, msg=extra["mode"])

    def _murim_live_body(self, scene_content: str, **extra) -> dict:
        return {
            "project_title": "청운검문의 진운",
            "purpose": "web_novel",
            "main_genre": "fantasy",
            "sub_genre": "male",
            "genre_detail": "murim",
            "main_genre_label": "판타지",
            "sub_genre_label": "남성향판타지",
            "genre_detail_label": "무협",
            "scene_title": "1화",
            "scene_content": scene_content,
            "world_setting": (
                "중국풍 무협. 경지는 삼류-이류-일류-절정-초절정-화경-현경으로 일관된다. "
                "정파/사파/마교 세력 대립과 은원이 서사의 축이다. "
                "화산파가 인의를 내세우면서 실제로는 단전을 뽑는 위선은 의도된 설정이다. "
                "정파답지 않다고 설정 붕괴로 보지 말 것. "
                "어투는 강호체 관용구를 쓰고, 현대어 어미만 '~하오'로 바꾸는 수준에 그치지 말 것."
            ),
            "character_profiles": {
                "진운": "청운검문 삼류무사. 회귀자다.",
                "청송": "화산파 장로. 정파 명분을 내세우되 실리는 잔혹하다. 의도된 위선.",
            },
            **extra,
        }

    def _assert_jianghu_idioms_not_just_hao(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        idiom_hits = (
            "은혜",
            "뼈에",
            "경지",
            "초절정",
            "대협",
            "소협",
            "문주",
            "장문",
            "강호",
            "은원",
            "의리",
            "내공",
            "무공",
            "단전",
            "검기",
            "기연",
            "화경",
            "현경",
            "절정",
            "사제",
            "문파",
        )
        self.assertTrue(any(hit in body for hit in idiom_hits), msg=body)

    def _assert_hypocrisy_is_distinguished(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip())
        intent_hits = ("의도", "위선", "이중", "명분", "설정", "구분", "가면", "표리")
        self.assertTrue(any(hit in body for hit in intent_hits), msg=body)
        self.assertNotRegex(
            body,
            r"(정파답지\s*않.{0,12}(오류|붕괴|문제)|정파라면.{0,16}(이러면\s*안|모순|오류)|장르\s*이탈)",
        )
        lowered = body.replace(" ", "")
        self.assertFalse(
            any(
                bad in lowered
                for bad in (
                    "정파답지않다오류",
                    "정파설정붕괴",
                    "정파가아니라오류",
                    "정파모순이문제",
                    "정파라면이러면안됨",
                )
            ),
            msg=body,
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_murim_continue_uses_jianghu_idioms(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._murim_live_body(
                MURIM_MODERN_SPEECH_SCENE,
                mode="continue",
                length_mode="short",
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_jianghu_idioms_not_just_hao(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_murim_rewrite_uses_jianghu_idioms(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._murim_live_body(
                MURIM_MODERN_SPEECH_SCENE,
                mode="rewrite",
                selected_text=MURIM_MODERN_SPEECH_SCENE,
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_jianghu_idioms_not_just_hao(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_murim_does_not_misread_orthodox_hypocrisy(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._murim_live_body(MURIM_HYPOCRISY_SCENE, mode="worldscan"),
        )
        self.assertEqual(status, 200, result)
        self._assert_hypocrisy_is_distinguished(str(result.get("text") or ""))


URBAN_SECRET_VS_PUBLIC_SCENE = (
    "각성자 민수는 정부 기밀 헌터였다. 동료에게도, 뉴스에도, 길드 명단에도 이름이 없었다. "
    "점심시간에 그는 강남역 승강장 한가운데서 각성 스킬 '참격'을 썼다. "
    "스마트폰 카메라 수십 대가 푸른 검기를 찍었고 실시간 방송 채팅이 폭발했다. "
    "다음 장면, 회사 회의실에서 김부장은 아무 일도 없었다는 듯 실적 보고를 받았다. "
    "뉴스도, 경찰도, SNS 해명도 없었다. 민수는 '이 힘은 나만의 비밀이야'라고 중얼거렸다."
)

TRADITIONAL_SYSTEM_WINDOW_SCENE = (
    "서부 왕국 견습 기사 에린이 숲길에서 검을 뽑았다. "
    "눈앞에 반투명한 시스템창이 떠올랐다. [레벨 업!] 검술 스킬이 15가 되었다. "
    "상태창의 HP가 가득 찼고, 스킬창에서 '파이어볼 Lv.3'을 선택했다."
)

TRADITIONAL_OVERNIGHT_GROWTH_SCENE = (
    "어제까지 불꽃 한 줌도 못 피우던 카엘이 하룻밤 잠을 자고 일어났다. "
    "수련도, 스승의 가르침도, 실패도 없었다. "
    "왕실 시험장에서 그는 대륙을 가르는 멸염을 한 번에 펼쳐 대마법사가 되었다. "
    "주변 석학들은 박수쳤다. 노력의 과정은 한 줄도 없었다."
)

URBAN_CROSS_PHRASES = (
    "현대 사회 시스템과 초자연적 힘의 공존 논리",
    "현실 사회 시스템(회사/언론/팬덤 등)을 다음 갈등 장치로",
    "현대 구어체와 실제 사회 용어",
)

TRADITIONAL_CROSS_PHRASES = (
    "성장 속도가 노력에 비례하는지 엄격히 확인",
    "노력/시행착오 기반의 다음 성장 단계",
    "게임적 어휘(레벨, 스킬 등) 없이",
)


class GenrePlaybookUrbanTraditionalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        app._GENRE_PLAYBOOKS_CACHE = None
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_urban_and_traditional_deltas(self) -> None:
        urban = app.load_genre_playbook_delta("fantasy", "male", "urban")
        self.assertIsInstance(urban, dict)
        self.assertIn("현실 세계의 논리(사회 제도, 언론, 법)와 초자연적 힘이 공존", urban["identity_addition"])
        self.assertIn("현대 사회 시스템과 초자연적 힘의 공존 논리", urban["group_rules_addition"]["A_judge"])
        self.assertIn("현실 사회 시스템(회사/언론/팬덤 등)을 다음 갈등 장치로", urban["group_rules_addition"]["B_suggest"])
        self.assertIn("현대 구어체와 실제 사회 용어", urban["group_rules_addition"]["C_style"])

        traditional = app.load_genre_playbook_delta("fantasy", "male", "traditional")
        self.assertIsInstance(traditional, dict)
        self.assertIn("회빙환·치트·시스템창 없는 로우파워 서구풍 정통 판타지", traditional["identity_addition"])
        self.assertIn("성장 속도가 노력에 비례하는지 엄격히 확인", traditional["group_rules_addition"]["A_judge"])
        self.assertIn("노력/시행착오 기반의 다음 성장 단계", traditional["group_rules_addition"]["B_suggest"])
        self.assertIn("게임적 어휘(레벨, 스킬 등) 없이", traditional["group_rules_addition"]["C_style"])

        self.assertNotEqual(urban["identity_addition"], traditional["identity_addition"])
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "male", ""))
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "female", "urban"))
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "female", "traditional"))
        self.assertIsNone(app.load_genre_playbook_delta("romance", "modern", "urban"))
        self.assertIsNone(app.load_genre_playbook_delta("romance", "romfant", "traditional"))

    def test_urban_builders_inject_delta_and_plain_male_does_not(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="urban",
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("[세부장르 추가 기준]", worldscan)
        self.assertIn("현대 사회 시스템과 초자연적 힘의 공존 논리", worldscan)
        self.assertIn("폭력성/전투 스케일 오판 방지", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[세부장르 추가 기준]"))
        self.assertLess(worldscan.find("[세부장르 추가 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="urban",
        )
        self.assertIn("[세부장르 추가 기준]", ideas)
        self.assertIn("현실 사회 시스템(회사/언론/팬덤 등)을 다음 갈등 장치로", ideas)
        self.assertIn("사이다 구조(위기→응징)", ideas)
        self.assertLess(ideas.find("[세부장르 추가 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고",
            "short",
            "",
            "",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="urban",
        )
        self.assertIn("[세부장르 추가 문체 기준]", continue_p)
        self.assertIn("현대 구어체와 실제 사회 용어", continue_p)
        self.assertIn("전투 장면은 타격감 있게", continue_p)
        self.assertLess(continue_p.find("[세부장르 추가 문체 기준]"), continue_p.find("[원고]"))

        analyze = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="urban",
        )
        self.assertIn("[장르별 판단 기준]", analyze)
        self.assertIn("[세부장르 추가 기준]", analyze)
        self.assertIn("현대 사회 시스템과 초자연적 힘의 공존 논리", analyze)

        plain_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="fantasy", sub_genre="male"
        )
        plain_analyze = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        for phrase in URBAN_CROSS_PHRASES + TRADITIONAL_CROSS_PHRASES:
            self.assertNotIn(phrase, plain_scan, msg=phrase)
            self.assertNotIn(phrase, plain_ideas, msg=phrase)
            self.assertNotIn(phrase, plain_continue, msg=phrase)
            self.assertNotIn(phrase, plain_analyze, msg=phrase)
        self.assertNotIn("[세부장르 추가 기준]", plain_scan)
        self.assertNotIn("[세부장르 추가 기준]", plain_ideas)
        self.assertNotIn("[세부장르 추가 문체 기준]", plain_continue)
        self.assertIn("폭력성/전투 스케일 오판 방지", plain_scan)
        self.assertIn("사이다 구조(위기→응징)", plain_ideas)
        self.assertIn("전투 장면은 타격감 있게", plain_continue)

    def test_traditional_builders_inject_delta_and_plain_male_does_not(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="traditional",
        )
        self.assertIn("[세부장르 추가 기준]", worldscan)
        self.assertIn("성장 속도가 노력에 비례하는지 엄격히 확인", worldscan)
        self.assertIn("폭력성/전투 스케일 오판 방지", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[세부장르 추가 기준]"))
        self.assertLess(worldscan.find("[세부장르 추가 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="traditional",
        )
        self.assertIn("[세부장르 추가 기준]", ideas)
        self.assertIn("노력/시행착오 기반의 다음 성장 단계", ideas)
        self.assertIn("사이다 구조(위기→응징)", ideas)

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고",
            "short",
            "",
            "",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="traditional",
        )
        self.assertIn("[세부장르 추가 문체 기준]", continue_p)
        self.assertIn("게임적 어휘(레벨, 스킬 등) 없이", continue_p)
        self.assertIn("전투 장면은 타격감 있게", continue_p)

        analyze = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="traditional",
        )
        self.assertIn("[세부장르 추가 기준]", analyze)
        self.assertIn("성장 속도가 노력에 비례하는지 엄격히 확인", analyze)

    def test_urban_and_traditional_do_not_mix(self) -> None:
        urban_a, urban_b, urban_c = URBAN_CROSS_PHRASES
        trad_a, trad_b, trad_c = TRADITIONAL_CROSS_PHRASES
        cases = (
            (
                "worldscan",
                app.SuperToryHandler._build_setting_break_scan_prompt,
                urban_a,
                trad_a,
            ),
            (
                "ideas",
                app.SuperToryHandler._build_next_idea_prompt,
                urban_b,
                trad_b,
            ),
            (
                "continue",
                lambda text, **kw: app.SuperToryHandler._build_continue_prompt(
                    text, "short", "", "", **kw
                ),
                urban_c,
                trad_c,
            ),
            (
                "analyze",
                app.SuperToryHandler._build_focused_analysis_prompt,
                urban_a,
                trad_a,
            ),
        )
        for label, builder, urban_must, trad_must in cases:
            urban = builder("한 줄 원고", main_genre="fantasy", sub_genre="male", genre_detail="urban")
            traditional = builder(
                "한 줄 원고", main_genre="fantasy", sub_genre="male", genre_detail="traditional"
            )
            self.assertIn(urban_must, urban, msg=label)
            self.assertNotIn(urban_must, traditional, msg=label)
            self.assertIn(trad_must, traditional, msg=label)
            self.assertNotIn(trad_must, urban, msg=label)
            for phrase in URBAN_CROSS_PHRASES:
                self.assertNotIn(phrase, traditional, msg=f"{label}:{phrase}")
            for phrase in TRADITIONAL_CROSS_PHRASES:
                self.assertNotIn(phrase, urban, msg=f"{label}:{phrase}")
            murim_phrase = "정파의 이중잣대가 의도적 설정인지"
            alt_phrase = "의도된 개변 여부 구분"
            self.assertNotIn(murim_phrase, urban, msg=label)
            self.assertNotIn(murim_phrase, traditional, msg=label)
            self.assertNotIn(alt_phrase, urban, msg=label)
            self.assertNotIn(alt_phrase, traditional, msg=label)

    def test_urban_traditional_dry_run_abc_api(self) -> None:
        specs = (
            (
                "urban",
                URBAN_SECRET_VS_PUBLIC_SCENE,
                (
                    {
                        "mode": "worldscan",
                        "heading": "[장르별 판단 기준]",
                        "extra": "[세부장르 추가 기준]",
                        "must": "현대 사회 시스템과 초자연적 힘의 공존 논리",
                        "needle": "[본문]",
                    },
                    {
                        "mode": "ideas",
                        "heading": "[장르별 판단 기준]",
                        "extra": "[세부장르 추가 기준]",
                        "must": "현실 사회 시스템(회사/언론/팬덤 등)을 다음 갈등 장치로",
                        "needle": "[현재 회차 본문]",
                    },
                    {
                        "mode": "continue",
                        "heading": "[장르별 문체 기준]",
                        "extra": "[세부장르 추가 문체 기준]",
                        "must": "현대 구어체와 실제 사회 용어",
                        "needle": "[원고]",
                        "length_mode": "short",
                    },
                ),
                URBAN_CROSS_PHRASES,
            ),
            (
                "traditional",
                TRADITIONAL_SYSTEM_WINDOW_SCENE,
                (
                    {
                        "mode": "worldscan",
                        "heading": "[장르별 판단 기준]",
                        "extra": "[세부장르 추가 기준]",
                        "must": "성장 속도가 노력에 비례하는지 엄격히 확인",
                        "needle": "[본문]",
                    },
                    {
                        "mode": "ideas",
                        "heading": "[장르별 판단 기준]",
                        "extra": "[세부장르 추가 기준]",
                        "must": "노력/시행착오 기반의 다음 성장 단계",
                        "needle": "[현재 회차 본문]",
                    },
                    {
                        "mode": "continue",
                        "heading": "[장르별 문체 기준]",
                        "extra": "[세부장르 추가 문체 기준]",
                        "must": "게임적 어휘(레벨, 스킬 등) 없이",
                        "needle": "[원고]",
                        "length_mode": "short",
                    },
                ),
                TRADITIONAL_CROSS_PHRASES,
            ),
        )
        for detail, scene, cases, own_phrases in specs:
            for extra in cases:
                heading = extra.pop("heading")
                extra_heading = extra.pop("extra")
                must = extra.pop("must")
                needle = extra.pop("needle")
                payload = {
                    "dry_run": True,
                    "project_title": "델타 검증",
                    "purpose": "web_novel",
                    "main_genre": "fantasy",
                    "sub_genre": "male",
                    "genre_detail": detail,
                    "scene_title": "1화",
                    "scene_content": scene,
                    **extra,
                }
                status, result = self.request("POST", "/api/ai/assist", payload)
                self.assertEqual(status, 200, result)
                full = str(result.get("full_prompt") or "")
                self.assertIn(heading, full, msg=f"{detail}/{extra['mode']}")
                self.assertIn(extra_heading, full, msg=f"{detail}/{extra['mode']}")
                self.assertEqual(full.count(heading), 1, msg=f"{detail}/{extra['mode']}")
                self.assertEqual(full.count(extra_heading), 1, msg=f"{detail}/{extra['mode']}")
                self.assertIn(must, full, msg=f"{detail}/{extra['mode']}")
                self.assertLess(full.find(heading), full.find(extra_heading), msg=f"{detail}/{extra['mode']}")
                self.assertLess(full.find(extra_heading), full.find(needle), msg=f"{detail}/{extra['mode']}")

                plain = {**payload, "project_title": "회귀한 영주", "genre_detail": ""}
                status, male = self.request("POST", "/api/ai/assist", plain)
                self.assertEqual(status, 200, male)
                male_full = str(male.get("full_prompt") or "")
                self.assertNotIn(extra_heading, male_full, msg=f"{detail}/{extra['mode']}")
                self.assertNotIn(must, male_full, msg=f"{detail}/{extra['mode']}")
                for phrase in own_phrases:
                    self.assertNotIn(phrase, male_full, msg=f"{detail}/{extra['mode']}:{phrase}")

    def _live_body(self, detail: str, scene_content: str, world_setting: str, **extra) -> dict:
        labels = {
            "urban": "현대판타지",
            "traditional": "정통판타지",
        }
        return {
            "project_title": "델타 라이브",
            "purpose": "web_novel",
            "main_genre": "fantasy",
            "sub_genre": "male",
            "genre_detail": detail,
            "main_genre_label": "판타지",
            "sub_genre_label": "남성향판타지",
            "genre_detail_label": labels[detail],
            "scene_title": "1화",
            "scene_content": scene_content,
            "world_setting": world_setting,
            "character_profiles": {
                "민수": "현대 각성자. 회사원.",
                "에린": "서부 왕국 견습 기사.",
                "카엘": "불꽃조차 못 피우던 견습 마법사.",
            },
            **extra,
        }

    def _assert_flags_secret_vs_public(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip(), msg="empty live response")
        hits = ("비밀", "공인", "모순", "불일치", "일관", "사회", "언론", "파장", "공개", "설정")
        self.assertTrue(any(hit in body for hit in hits), msg=body)
        problem = ("붕괴", "모순", "불일치", "어긋", "충돌", "문제", "일관되지", "안 맞")
        self.assertTrue(any(hit in body for hit in problem), msg=body)

    def _assert_flags_system_window_mix(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip(), msg="empty live response")
        hits = ("시스템창", "레벨", "게임", "스킬", "정통", "혼입", "섞")
        self.assertTrue(any(hit in body for hit in hits), msg=body)

    def _assert_flags_overnight_growth(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip(), msg="empty live response")
        hits = ("성장", "노력", "하루", "하룻밤", "급성장", "과정", "수련", "생략", "비례", "개연")
        self.assertTrue(any(hit in body for hit in hits), msg=body)
        compact = body.replace(" ", "")
        self.assertFalse(
            any(
                bad in compact
                for bad in ("문제없다", "지적할점없음", "급성장이자연", "하룻밤은괜찮")
            ),
            msg=body,
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_urban_flags_secret_vs_public_inconsistency(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._live_body(
                "urban",
                URBAN_SECRET_VS_PUBLIC_SCENE,
                "현대 서울. 각성자와 던전이 존재한다. "
                "헌터 능력이 사회적으로 공인된 직업인지, 철저히 비밀인지는 작품 안에서 하나로 일관돼야 한다.",
                mode="worldscan",
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_flags_secret_vs_public(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_traditional_flags_system_window_mix(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._live_body(
                "traditional",
                TRADITIONAL_SYSTEM_WINDOW_SCENE,
                "서구풍 정통 판타지. 시스템창·레벨·스킬창은 없다. 마법과 검술은 수련으로 체득한다.",
                mode="worldscan",
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_flags_system_window_mix(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_traditional_flags_overnight_growth_on_feedback(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._live_body(
                "traditional",
                TRADITIONAL_OVERNIGHT_GROWTH_SCENE,
                "서구풍 정통 판타지. 성장은 노력과 시행착오에 비례한다. 설명 없는 급성장은 허용하지 않는다.",
                mode="analyze",
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_flags_overnight_growth(str(result.get("text") or ""))


HIDDEN_WORLD_MASQUERADE_BREAK_SCENE = (
    "서울 여의도 생방송 카메라 앞에서 구미호가 아홉 꼬리를 펼쳤다. "
    "앵커가 '요괴가 실존합니다'라고 말했고, 포털 메인과 대통령 브리핑까지 초자연 존재가 공식 발표됐다. "
    "이면세계의 은닉 규칙이나 발각 후 수습은 한 줄도 없었다. "
    "다음 장면, 주인공 한서는 편의점에서 삼각김밥을 사고 아무 일도 없었다는 듯 출근했다."
)

HIDDEN_WORLD_INSTANT_MUNCHKIN_SCENE = (
    "첫 장부터 한서는 이면세계의 왕이었다. 발견도, 적응도, 미숙함도 없었다. "
    "구미호 족장과 뱀파이어 공의회가 그에게 무릎을 꿇었고, "
    "그는 하품하며 도시 하나를 손짓으로 지웠다. "
    "평범한 회사원에서 시작한다는 언급은 없다."
)

HIDDEN_WORLD_CROSS_PHRASES = (
    "마스커레이드·이면세계 규칙 일관성 확인",
    "이면세계 확장(새 신화 존재/세력과의 조우)",
    "미스터리/오컬트 분위기",
)


class GenrePlaybookHiddenWorldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        app._GENRE_PLAYBOOKS_CACHE = None
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_hidden_world_delta(self) -> None:
        hidden = app.load_genre_playbook_delta("fantasy", "male", "hidden_world")
        self.assertIsInstance(hidden, dict)
        self.assertIn("마스커레이드(초자연 존재를 일반 사회로부터 숨기는 규칙)", hidden["identity_addition"])
        self.assertIn("마스커레이드·이면세계 규칙 일관성 확인", hidden["group_rules_addition"]["A_judge"])
        self.assertIn("이면세계 확장(새 신화 존재/세력과의 조우)", hidden["group_rules_addition"]["B_suggest"])
        self.assertIn("미스터리/오컬트 분위기", hidden["group_rules_addition"]["C_style"])
        urban = app.load_genre_playbook_delta("fantasy", "male", "urban")
        self.assertIsInstance(urban, dict)
        self.assertNotEqual(hidden["identity_addition"], urban["identity_addition"])
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "male", ""))
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "female", "hidden_world"))
        self.assertIsNone(app.load_genre_playbook_delta("romance", "modern", "hidden_world"))

    def test_hidden_world_builders_inject_delta_and_plain_male_does_not(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="hidden_world",
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("[세부장르 추가 기준]", worldscan)
        self.assertIn("마스커레이드·이면세계 규칙 일관성 확인", worldscan)
        self.assertIn("폭력성/전투 스케일 오판 방지", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[세부장르 추가 기준]"))
        self.assertLess(worldscan.find("[세부장르 추가 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="hidden_world",
        )
        self.assertIn("[세부장르 추가 기준]", ideas)
        self.assertIn("이면세계 확장(새 신화 존재/세력과의 조우)", ideas)
        self.assertIn("사이다 구조(위기→응징)", ideas)
        self.assertLess(ideas.find("[세부장르 추가 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고",
            "short",
            "",
            "",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="hidden_world",
        )
        self.assertIn("[세부장르 추가 문체 기준]", continue_p)
        self.assertIn("미스터리/오컬트 분위기", continue_p)
        self.assertIn("전투 장면은 타격감 있게", continue_p)
        self.assertLess(continue_p.find("[세부장르 추가 문체 기준]"), continue_p.find("[원고]"))

        analyze = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="hidden_world",
        )
        self.assertIn("[세부장르 추가 기준]", analyze)
        self.assertIn("마스커레이드·이면세계 규칙 일관성 확인", analyze)

        plain_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="fantasy", sub_genre="male"
        )
        for phrase in HIDDEN_WORLD_CROSS_PHRASES:
            self.assertNotIn(phrase, plain_scan, msg=phrase)
            self.assertNotIn(phrase, plain_ideas, msg=phrase)
            self.assertNotIn(phrase, plain_continue, msg=phrase)
        self.assertNotIn("[세부장르 추가 기준]", plain_scan)
        self.assertNotIn("[세부장르 추가 문체 기준]", plain_continue)
        self.assertIn("폭력성/전투 스케일 오판 방지", plain_scan)

    def test_hidden_world_does_not_mix_with_urban(self) -> None:
        cases = (
            (
                "worldscan",
                app.SuperToryHandler._build_setting_break_scan_prompt,
                HIDDEN_WORLD_CROSS_PHRASES[0],
                URBAN_CROSS_PHRASES[0],
            ),
            (
                "ideas",
                app.SuperToryHandler._build_next_idea_prompt,
                HIDDEN_WORLD_CROSS_PHRASES[1],
                URBAN_CROSS_PHRASES[1],
            ),
            (
                "continue",
                lambda text, **kw: app.SuperToryHandler._build_continue_prompt(
                    text, "short", "", "", **kw
                ),
                HIDDEN_WORLD_CROSS_PHRASES[2],
                URBAN_CROSS_PHRASES[2],
            ),
        )
        for label, builder, hidden_must, urban_must in cases:
            hidden = builder(
                "한 줄 원고", main_genre="fantasy", sub_genre="male", genre_detail="hidden_world"
            )
            urban = builder(
                "한 줄 원고", main_genre="fantasy", sub_genre="male", genre_detail="urban"
            )
            self.assertIn(hidden_must, hidden, msg=label)
            self.assertNotIn(hidden_must, urban, msg=label)
            self.assertIn(urban_must, urban, msg=label)
            self.assertNotIn(urban_must, hidden, msg=label)
            for phrase in HIDDEN_WORLD_CROSS_PHRASES:
                self.assertNotIn(phrase, urban, msg=f"{label}:{phrase}")
            for phrase in URBAN_CROSS_PHRASES:
                self.assertNotIn(phrase, hidden, msg=f"{label}:{phrase}")

    def test_hidden_world_dry_run_abc_api(self) -> None:
        cases = (
            {
                "mode": "worldscan",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "마스커레이드·이면세계 규칙 일관성 확인",
                "needle": "[본문]",
            },
            {
                "mode": "ideas",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "이면세계 확장(새 신화 존재/세력과의 조우)",
                "needle": "[현재 회차 본문]",
            },
            {
                "mode": "continue",
                "heading": "[장르별 문체 기준]",
                "extra": "[세부장르 추가 문체 기준]",
                "must": "미스터리/오컬트 분위기",
                "needle": "[원고]",
                "length_mode": "short",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            extra_heading = extra.pop("extra")
            must = extra.pop("must")
            needle = extra.pop("needle")
            payload = {
                "dry_run": True,
                "project_title": "이면의 서울",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "hidden_world",
                "scene_title": "1화",
                "scene_content": HIDDEN_WORLD_MASQUERADE_BREAK_SCENE,
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertIn(extra_heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertEqual(full.count(extra_heading), 1, msg=extra["mode"])
            self.assertIn(must, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(extra_heading), msg=extra["mode"])
            self.assertLess(full.find(extra_heading), full.find(needle), msg=extra["mode"])
            for phrase in URBAN_CROSS_PHRASES:
                self.assertNotIn(phrase, full, msg=f"{extra['mode']}:{phrase}")

            plain = {**payload, "project_title": "회귀한 영주", "genre_detail": ""}
            status, male = self.request("POST", "/api/ai/assist", plain)
            self.assertEqual(status, 200, male)
            male_full = str(male.get("full_prompt") or "")
            self.assertNotIn(extra_heading, male_full, msg=extra["mode"])
            self.assertNotIn(must, male_full, msg=extra["mode"])

            urban_payload = {**payload, "project_title": "각성 길드", "genre_detail": "urban"}
            status, urban = self.request("POST", "/api/ai/assist", urban_payload)
            self.assertEqual(status, 200, urban)
            urban_full = str(urban.get("full_prompt") or "")
            self.assertNotIn(must, urban_full, msg=extra["mode"])
            self.assertIn(URBAN_CROSS_PHRASES[0] if extra["mode"] == "worldscan" else (
                URBAN_CROSS_PHRASES[1] if extra["mode"] == "ideas" else URBAN_CROSS_PHRASES[2]
            ), urban_full, msg=extra["mode"])

    def _live_body(self, detail: str, scene_content: str, world_setting: str, **extra) -> dict:
        labels = {
            "hidden_world": "어반판타지",
            "urban": "현대판타지",
        }
        return {
            "project_title": "이면의 서울",
            "purpose": "web_novel",
            "main_genre": "fantasy",
            "sub_genre": "male",
            "genre_detail": detail,
            "main_genre_label": "판타지",
            "sub_genre_label": "남성향판타지",
            "genre_detail_label": labels[detail],
            "scene_title": "1화",
            "scene_content": scene_content,
            "world_setting": world_setting,
            "character_profiles": {
                "한서": "평범한 회사원. 이면세계를 이제 막 알게 된 인물이어야 한다.",
            },
            **extra,
        }

    def _assert_flags_masquerade_break(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip(), msg="empty live response")
        hits = ("마스커레이드", "비밀", "은닉", "발각", "공개", "뉴스", "이면", "모순", "붕괴", "일관")
        self.assertTrue(any(hit in body for hit in hits), msg=body)
        problem = ("붕괴", "모순", "불일치", "어긋", "충돌", "문제", "일관되지", "숨기")
        self.assertTrue(any(hit in body for hit in problem), msg=body)

    def _assert_flags_instant_munchkin(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip(), msg="empty live response")
        hits = ("먼치킨", "성장", "발견", "처음부터", "약자", "곡선", "미숙", "적응")
        self.assertTrue(any(hit in body for hit in hits), msg=body)
        mismatch = ("어긋", "다르", "충돌", "기대", "장르", "발견→적응", "약자")
        self.assertTrue(any(hit in body for hit in mismatch), msg=body)
        twist = ("의도", "비틀", "구분", "설정이라면", "의도된")
        self.assertTrue(any(hit in body for hit in twist), msg=body)

    def _assert_urban_does_not_require_masquerade(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip(), msg="empty live response")
        compact = body.replace(" ", "")
        forbidden = (
            "마스커레이드붕괴",
            "숨겨야만한다",
            "이면세계규칙위반",
            "비밀로유지해야",
            "일반인에게비밀이어야",
        )
        self.assertFalse(any(bad in compact for bad in forbidden), msg=body)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_hidden_world_flags_masquerade_break(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._live_body(
                "hidden_world",
                HIDDEN_WORLD_MASQUERADE_BREAK_SCENE,
                "현대 서울. 요괴·뱀파이어 등 초자연 존재가 인간 사회에 숨어 산다. "
                "마스커레이드(일반인에게 비밀)가 핵심 규칙이다.",
                mode="worldscan",
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_flags_masquerade_break(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_hidden_world_flags_instant_munchkin(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._live_body(
                "hidden_world",
                HIDDEN_WORLD_INSTANT_MUNCHKIN_SCENE,
                "현대 서울 이면세계. 평범한 주인공이 우연히 조우하며 발견→적응→성장해야 한다. "
                "처음부터 최강인 먼치킨은 장르 기대와 어긋난다. 의도된 비틀기인지는 구분해 보라.",
                mode="analyze",
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_flags_instant_munchkin(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_urban_accepts_public_hunters_without_masquerade(self) -> None:
        public_hunters = (
            "각성자 협회가 생방송으로 이번 달 랭커를 발표했다. "
            "민수가 S급 헌터로 호명되자 시청자와 포털 메인이 환호했다. "
            "길드 앱에는 그의 스킬 목록이 공개되어 있었고, "
            "경찰은 던전 출동을 공식 브리핑했다."
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._live_body(
                "urban",
                public_hunters,
                "현대 한국. 헌터 능력은 길드·협회·언론을 통해 사회적으로 공인되어 있다. "
                "초자연 존재를 숨기는 마스커레이드 규칙은 없다.",
                mode="worldscan",
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_urban_does_not_require_masquerade(str(result.get("text") or ""))


SPORTS_WRONG_OFFSIDE_SCENE = (
    "후반 32분, 민호가 상대 골문 바로 앞에서 공을 잡았다. "
    "골키퍼와 수비수 전원이 하프라인 뒤에 남아 있었는데도 "
    "주심은 '공격수가 공보다 뒤에 있으면 오프사이드'라며 휘슬을 불었다. "
    "민호는 손을 들어 공을 잡아 네트에 넣고 득점을 인정받았다. "
    "해설은 코너킥에서 손으로 넣어도 골이라고 설명했다."
)

SPORTS_INTENDED_PLAYER_TWIST_SCENE = (
    "잠실 야구장. 선발 투수 손흥민이 160km 직구를 꽂아 삼진을 잡았다. "
    "관중은 국가대표 야구 에이스의 이름에 환호했고, "
    "민호는 덕아웃에서 '형, 월드시리즈 가자'고 외쳤다. "
    "축구 국가대표 이야기는 한 줄도 없다. 이 세계에서 손흥민은 처음부터 야구 선수다."
)

SPORTS_CROSS_PHRASES = (
    "종목 규칙/전술 정확성",
    "다음 시합/훈련 사이클",
    "종목 전문 용어와 경기 상황 묘사",
)

OTHER_DELTA_CROSS_PHRASES = (
    *MURIM_CROSS_PHRASES,
    *ALT_HISTORY_CROSS_PHRASES,
    *URBAN_CROSS_PHRASES,
    *TRADITIONAL_CROSS_PHRASES,
    *HIDDEN_WORLD_CROSS_PHRASES,
)


class GenrePlaybookSportsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app._GENRE_PLAYBOOKS_CACHE = None
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
        app._GENRE_PLAYBOOKS_CACHE = None
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_load_sports_delta(self) -> None:
        sports = app.load_genre_playbook_delta("fantasy", "male", "sports")
        self.assertIsInstance(sports, dict)
        self.assertIn("특정 종목 정상 도달", sports["identity_addition"])
        self.assertIn("종목 규칙/전술 정확성", sports["group_rules_addition"]["A_judge"])
        self.assertIn("다음 시합/훈련 사이클", sports["group_rules_addition"]["B_suggest"])
        self.assertIn("종목 전문 용어와 경기 상황 묘사", sports["group_rules_addition"]["C_style"])
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "male", ""))
        self.assertIsNone(app.load_genre_playbook_delta("fantasy", "female", "sports"))
        self.assertIsNone(app.load_genre_playbook_delta("romance", "modern", "sports"))

    def test_sports_builders_inject_delta_and_plain_male_does_not(self) -> None:
        worldscan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="sports",
        )
        self.assertIn("[장르별 판단 기준]", worldscan)
        self.assertIn("[세부장르 추가 기준]", worldscan)
        self.assertIn("종목 규칙/전술 정확성", worldscan)
        self.assertIn("폭력성/전투 스케일 오판 방지", worldscan)
        self.assertLess(worldscan.find("[장르별 판단 기준]"), worldscan.find("[세부장르 추가 기준]"))
        self.assertLess(worldscan.find("[세부장르 추가 기준]"), worldscan.find("[본문]"))

        ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="sports",
        )
        self.assertIn("[세부장르 추가 기준]", ideas)
        self.assertIn("다음 시합/훈련 사이클", ideas)
        self.assertIn("사이다 구조(위기→응징)", ideas)
        self.assertLess(ideas.find("[세부장르 추가 기준]"), ideas.find("[현재 회차 본문]"))

        continue_p = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고",
            "short",
            "",
            "",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="sports",
        )
        self.assertIn("[세부장르 추가 문체 기준]", continue_p)
        self.assertIn("종목 전문 용어와 경기 상황 묘사", continue_p)
        self.assertIn("전투 장면은 타격감 있게", continue_p)
        self.assertLess(continue_p.find("[세부장르 추가 문체 기준]"), continue_p.find("[원고]"))

        analyze = app.SuperToryHandler._build_focused_analysis_prompt(
            "한 줄 원고",
            main_genre="fantasy",
            sub_genre="male",
            genre_detail="sports",
        )
        self.assertIn("[세부장르 추가 기준]", analyze)
        self.assertIn("종목 규칙/전술 정확성", analyze)

        plain_scan = app.SuperToryHandler._build_setting_break_scan_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_ideas = app.SuperToryHandler._build_next_idea_prompt(
            "한 줄 원고", main_genre="fantasy", sub_genre="male"
        )
        plain_continue = app.SuperToryHandler._build_continue_prompt(
            "한 줄 원고", "short", "", "", main_genre="fantasy", sub_genre="male"
        )
        for phrase in SPORTS_CROSS_PHRASES:
            self.assertNotIn(phrase, plain_scan, msg=phrase)
            self.assertNotIn(phrase, plain_ideas, msg=phrase)
            self.assertNotIn(phrase, plain_continue, msg=phrase)
        self.assertNotIn("[세부장르 추가 기준]", plain_scan)
        self.assertNotIn("[세부장르 추가 문체 기준]", plain_continue)
        self.assertIn("폭력성/전투 스케일 오판 방지", plain_scan)

    def test_sports_does_not_mix_with_other_deltas(self) -> None:
        cases = (
            (
                "worldscan",
                app.SuperToryHandler._build_setting_break_scan_prompt,
                SPORTS_CROSS_PHRASES[0],
            ),
            (
                "ideas",
                app.SuperToryHandler._build_next_idea_prompt,
                SPORTS_CROSS_PHRASES[1],
            ),
            (
                "continue",
                lambda text, **kw: app.SuperToryHandler._build_continue_prompt(
                    text, "short", "", "", **kw
                ),
                SPORTS_CROSS_PHRASES[2],
            ),
        )
        other_details = ("murim", "alt_history", "urban", "hidden_world", "traditional")
        for label, builder, sports_must in cases:
            sports = builder(
                "한 줄 원고", main_genre="fantasy", sub_genre="male", genre_detail="sports"
            )
            self.assertIn(sports_must, sports, msg=label)
            for detail in other_details:
                other = builder(
                    "한 줄 원고", main_genre="fantasy", sub_genre="male", genre_detail=detail
                )
                self.assertNotIn(sports_must, other, msg=f"{label}:{detail}")
                for phrase in SPORTS_CROSS_PHRASES:
                    self.assertNotIn(phrase, other, msg=f"{label}:{detail}:{phrase}")
            for phrase in OTHER_DELTA_CROSS_PHRASES:
                self.assertNotIn(phrase, sports, msg=f"{label}:{phrase}")

    def test_sports_dry_run_abc_api(self) -> None:
        cases = (
            {
                "mode": "worldscan",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "종목 규칙/전술 정확성",
                "needle": "[본문]",
            },
            {
                "mode": "ideas",
                "heading": "[장르별 판단 기준]",
                "extra": "[세부장르 추가 기준]",
                "must": "다음 시합/훈련 사이클",
                "needle": "[현재 회차 본문]",
            },
            {
                "mode": "continue",
                "heading": "[장르별 문체 기준]",
                "extra": "[세부장르 추가 문체 기준]",
                "must": "종목 전문 용어와 경기 상황 묘사",
                "needle": "[원고]",
                "length_mode": "short",
            },
        )
        for extra in cases:
            heading = extra.pop("heading")
            extra_heading = extra.pop("extra")
            must = extra.pop("must")
            needle = extra.pop("needle")
            payload = {
                "dry_run": True,
                "project_title": "골라인의 왕",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "sports",
                "scene_title": "1화",
                "scene_content": SPORTS_WRONG_OFFSIDE_SCENE,
                **extra,
            }
            status, result = self.request("POST", "/api/ai/assist", payload)
            self.assertEqual(status, 200, result)
            full = str(result.get("full_prompt") or "")
            self.assertIn(heading, full, msg=extra["mode"])
            self.assertIn(extra_heading, full, msg=extra["mode"])
            self.assertEqual(full.count(heading), 1, msg=extra["mode"])
            self.assertEqual(full.count(extra_heading), 1, msg=extra["mode"])
            self.assertIn(must, full, msg=extra["mode"])
            self.assertLess(full.find(heading), full.find(extra_heading), msg=extra["mode"])
            self.assertLess(full.find(extra_heading), full.find(needle), msg=extra["mode"])
            for phrase in OTHER_DELTA_CROSS_PHRASES:
                self.assertNotIn(phrase, full, msg=f"{extra['mode']}:{phrase}")

            plain = {**payload, "project_title": "회귀한 영주", "genre_detail": ""}
            status, male = self.request("POST", "/api/ai/assist", plain)
            self.assertEqual(status, 200, male)
            male_full = str(male.get("full_prompt") or "")
            self.assertNotIn(extra_heading, male_full, msg=extra["mode"])
            self.assertNotIn(must, male_full, msg=extra["mode"])

    def _live_body(self, scene_content: str, world_setting: str, **extra) -> dict:
        return {
            "project_title": "골라인의 왕",
            "purpose": "web_novel",
            "main_genre": "fantasy",
            "sub_genre": "male",
            "genre_detail": "sports",
            "main_genre_label": "판타지",
            "sub_genre_label": "남성향판타지",
            "genre_detail_label": "스포츠물",
            "scene_title": "1화",
            "scene_content": scene_content,
            "world_setting": world_setting,
            "character_profiles": {
                "민호": "2부 리그 축구 유망주. 주전 경쟁 중이다.",
                "손흥민": "이 세계의 야구 국가대표 에이스. 축구 선수가 아니다. 의도된 각색.",
            },
            **extra,
        }

    def _assert_flags_wrong_offside(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip(), msg="empty live response")
        hits = ("오프사이드", "규칙", "핸드볼", "손", "골키퍼", "잘못", "틀린", "오류")
        self.assertTrue(any(hit in body for hit in hits), msg=body)
        problem = ("붕괴", "모순", "불일치", "어긋", "오류", "잘못", "틀린", "규칙")
        self.assertTrue(any(hit in body for hit in problem), msg=body)

    def _assert_intended_player_twist_not_flagged(self, text: str) -> None:
        body = str(text or "")
        self.assertTrue(body.strip(), msg="empty live response")
        recognized = ("의도", "각색", "개변", "장르 장치", "핵심 설정")
        self.assertTrue(any(hit in body for hit in recognized), msg=body)
        self.assertNotRegex(
            body,
            r"(고증\s*오류|실존\s*선수.{0,12}오류|축구\s*선수여야|야구\s*설정.{0,8}(오류|문제))",
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_sports_flags_wrong_offside(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._live_body(
                SPORTS_WRONG_OFFSIDE_SCENE,
                "현대 한국 축구. 실제 축구 규칙(오프사이드, 핸드볼 등)을 따른다. "
                "규칙이 명백히 틀리면 설정 붕괴로 짚어야 한다.",
                mode="worldscan",
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_flags_wrong_offside(str(result.get("text") or ""))

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_sports_accepts_intended_player_twist(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self._live_body(
                SPORTS_INTENDED_PLAYER_TWIST_SCENE,
                "스포츠물. 핵심 설정은 손흥민이 축구가 아니라 야구 국가대표라는 의도된 각색이다. "
                "실제 행적과 다른 것은 고증 오류가 아니라 작품의 장르 장치다. "
                "대체역사 델타와 같이 의도된 각색은 오류로 보지 마라.",
                mode="worldscan",
            ),
        )
        self.assertEqual(status, 200, result)
        self._assert_intended_player_twist_not_flagged(str(result.get("text") or ""))


if __name__ == "__main__":
    unittest.main()


