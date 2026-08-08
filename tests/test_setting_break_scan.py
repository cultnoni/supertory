"""설정 붕괴 감지기(worldscan) prompt + judgment smoke checks."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


class SettingBreakScanTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_prompt_builder_has_new_contract(self) -> None:
        prompt = app.SuperToryHandler._build_setting_break_scan_prompt("한 줄 원고")
        self.assertIn("[현재 작업]", prompt)
        self.assertIn("회빙환", prompt)
        self.assertIn("[검사 결과]", prompt)
        self.assertNotIn("세계관 수호자", prompt)
        self.assertNotIn("Core Identity", prompt)

    def test_dry_run_uses_indexed_or_task_prompt(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "설정붕괴", "main_genre": "판타지", "sub_genre": "무협"},
        )
        self.assertEqual(status, 201)
        status, chapter = self.request(
            "POST", f"/api/projects/{project['id']}/chapters", {"title": "1권"}
        )
        self.assertEqual(status, 201)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201)
        indexed = (
            "[프로젝트 누적 정보 - 참고용]\n"
            "등장인물: [\"서연\"]\n"
            "세계관 설정: 조선 풍 동양 판타지, 스마트폰·인터넷 없음\n\n"
            + app.SuperToryHandler._build_setting_break_scan_prompt(
                "그는 품에서 스마트폰을 꺼내 지도 앱을 켰다."
            )
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worldscan",
                "dry_run": True,
                "project_id": project["id"],
                "project_title": "설정붕괴",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "1화",
                "scene_content": "그는 품에서 스마트폰을 꺼내 지도 앱을 켰다.",
                "world_setting": "조선 풍 산천. 전자기기·인터넷은 존재하지 않는다.",
                "character_profiles": {"서연": "현대에서 환생한 회귀자"},
                "indexed_prompt": indexed,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(result.get("indexed_prompt_present"))
        full = result.get("full_prompt") or ""
        self.assertIn("설정과 어긋나는 지점", full)
        self.assertIn("회빙환", full)
        self.assertNotIn("세계관 수호자 스캔 결과", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_judgment_anachronism_vs_reincarnator_monologue(self) -> None:
        world = (
            "동양풍 무협 세계관. 칼과 내공만 존재한다. "
            "스마트폰·인터넷·앱 등 현대 문물은 없다."
        )
        profiles = {
            "강서연": "현대 대한민국에서 환생(회빙환)한 주인공. 전생 기억을 갖고 있다.",
            "검객 묵연": "이 세계 토착 무사. 현대 지식을 모른다.",
        }
        case_a = (
            "산길을 걷던 묵연은 품에서 스마트폰을 꺼내 지도 앱으로 경로를 확인했다. "
            "근처 카페에서 커피를 사 마실 생각도 했다."
        )
        case_b = (
            "강서연은 속으로 중얼거렸다. "
            "'전생에 쓰던 스마트폰 지도 앱이 있었으면 이 산길도 금방 빠져나갔을 텐데.' "
            "그녀는 검집에 손을 얹고 걸음을 옮겼다."
        )

        def run_case(text: str) -> str:
            indexed = (
                "[프로젝트 누적 정보 - 참고용]\n"
                '등장인물: ["강서연", "검객 묵연"]\n'
                "세계관 설정: 동양풍 무협, 현대 문물 없음, 강서연은 회빙환\n"
                "지금까지 줄거리: 강서연이 환생 후 산길을 걷는다\n"
                "미회수 복선: \n\n"
                + app.SuperToryHandler._build_setting_break_scan_prompt(text)
            )
            status, result = self.request(
                "POST",
                "/api/ai/assist",
                {
                    "mode": "worldscan",
                    "project_title": "설정붕괴 판정",
                    "main_genre": "판타지",
                    "sub_genre": "무협",
                    "main_genre_label": "판타지",
                    "sub_genre_label": "무협",
                    "keywords": ["동양풍", "무협", "회빙환"],
                    "scene_title": "산길",
                    "scene_content": text,
                    "world_setting": world,
                    "character_profiles": profiles,
                    "indexed_prompt": indexed,
                    "system_instruction_vars": {
                        "project_genre_main": "판타지",
                        "project_genre_sub": "무협",
                        "world_setting_keywords": ["동양풍", "무협", "회빙환"],
                    },
                },
            )
            self.assertEqual(status, 200, result)
            return str(result.get("text") or "")

        out_a = run_case(case_a)
        out_b = run_case(case_b)
        # (a) narration anachronism should be flagged
        self.assertTrue(
            ("세계관" in out_a) or ("스마트폰" in out_a) or ("어긋" in out_a) or ("문제" in out_a),
            f"case A should flag anachronism:\n{out_a}",
        )
        self.assertNotIn("발견되지 않았습니다", out_a)
        # (b) reincarnator internal thought should usually pass
        self.assertTrue(
            ("발견되지 않았습니다" in out_b)
            or ("해당 없음" in out_b)
            or ("지적할" in out_b and "없" in out_b)
            or out_b.count("유형:") == 0,
            f"case B should not flag reincarnator monologue:\n{out_b}",
        )
        print("\n=== CASE A (should flag) ===\n", out_a)
        print("\n=== CASE B (should pass) ===\n", out_b)
