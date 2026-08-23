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
                self.assertEqual(fmt(main, sub, "alt_history"), base)
                if (main, sub) == ("romance", "modern"):
                    historical = fmt(main, sub, "historical")
                    self.assertNotEqual(historical, base)
                    self.assertIn("[세부장르 추가", historical)
                    self.assertEqual(fmt(main, sub, "oriental_romfant"), base)
                elif (main, sub) == ("romance", "romfant"):
                    oriental = fmt(main, sub, "oriental_romfant")
                    self.assertNotEqual(oriental, base)
                    self.assertIn("[세부장르 추가", oriental)
                    self.assertEqual(fmt(main, sub, "historical"), base)
                else:
                    self.assertEqual(fmt(main, sub, "historical"), base)
                    self.assertEqual(fmt(main, sub, "oriental_romfant"), base)
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
                "title": "대체역사 델타 없음",
                "purpose": "web_novel",
                "main_genre": "fantasy",
                "sub_genre": "male",
                "genre_detail": "alt_history",
            },
        )
        self.assertEqual(status, 201, project)
        self.assertEqual(project.get("genre_detail"), "alt_history")
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
                    "project_title": "대체역사 델타 없음",
                    "purpose": "web_novel",
                    "main_genre": "fantasy",
                    "sub_genre": "male",
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


if __name__ == "__main__":
    unittest.main()

