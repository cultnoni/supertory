"""Next-idea suggestions (ideas mode) wiring checks."""

from __future__ import annotations

import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


SAMPLE_SCENE = """
주막 안. 서연은 잔을 내려놓으며 묵연의 눈을 피했다.
"그 문양… 어디서 본 것 같아." 묵연이 낮게 말했다.
서연의 소매 안쪽, 은빛 문양이 촛불에 잠깐 빛났다.
그녀는 소매를 고쳐 입으며 웃었다. "착각이겠죠."
문 밖에서 말발굽 소리가 가까워졌다. 묵연의 손이 칼자루로 갔다.
서연은 잔을 다시 집어 들었다. 손이 아주 미세하게 떨리고 있었다.
""".strip()

OPEN_THREAD = "소매 안 은빛 문양의 정체 (아직 밝히지 않음)"


class NextIdeaTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_prompt_contract(self) -> None:
        prompt = app.SuperToryHandler._build_next_idea_prompt(SAMPLE_SCENE)
        self.assertIn("[현재 작업]", prompt)
        self.assertIn("미회수 복선", prompt)
        self.assertIn("**후보 N:", prompt)
        self.assertIn("[현재 회차 본문]", prompt)
        self.assertIn("[다음 아이디어 제안]", prompt)
        self.assertNotIn("Core Identity", prompt)
        self.assertNotIn("5~8개", prompt)

    def test_dry_run_uses_indexed_task_prompt(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "아이디어", "main_genre": "판타지", "sub_genre": "무협"},
        )
        self.assertEqual(status, 201)
        indexed = (
            "[프로젝트 누적 정보 - 참고용]\n"
            '등장인물: ["서연", "묵연"]\n'
            "세계관 설정: 동양풍 무협\n"
            "지금까지 줄거리: 주막에서 은빛 문양이 스친다\n"
            f"미회수 복선: {OPEN_THREAD}\n\n"
            + app.SuperToryHandler._build_next_idea_prompt(SAMPLE_SCENE)
            + f"\n\n[본문]\n{SAMPLE_SCENE}"
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "ideas",
                "dry_run": True,
                "project_id": project["id"],
                "project_title": "아이디어",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "주막",
                "scene_content": SAMPLE_SCENE,
                "indexed_prompt": indexed,
            },
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("indexed_prompt_present"))
        self.assertTrue(result.get("indexed_prompt_has_index_block"))
        full = result.get("full_prompt") or ""
        self.assertIn("다음 전개", full)
        self.assertIn("미회수 복선", full)
        self.assertIn(OPEN_THREAD, full)
        self.assertIn("[다음 아이디어 제안]", full)
        self.assertNotIn("5~8개", full)
        self.assertNotIn("짧은 불릿 목록", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_ideas_cover_open_thread_and_variety(self) -> None:
        indexed = (
            "[프로젝트 누적 정보 - 참고용]\n"
            '등장인물: ["서연(현대 환생자, 문양을 숨김)", "묵연(담담한 무사, 관찰력 좋음)"]\n'
            "세계관 설정: 동양풍 무협, 현대 문물 없음\n"
            "지금까지 줄거리: 두 사람이 주막에서 만나고 은빛 문양이 비친다\n"
            f"미회수 복선: {OPEN_THREAD}\n\n"
            + app.SuperToryHandler._build_next_idea_prompt(SAMPLE_SCENE)
            + f"\n\n[본문]\n{SAMPLE_SCENE}"
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "ideas",
                "project_title": "미회수 복선 테스트",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "keywords": ["동양풍", "무협"],
                "scene_title": "주막",
                "scene_content": SAMPLE_SCENE,
                "indexed_prompt": indexed,
                "world_setting": "동양풍 무협. 칼과 내공.",
                "character_profiles": {
                    "서연": "현대에서 환생한 주인공. 소매 문양을 숨긴다.",
                    "묵연": "담담한 토착 무사. 관찰력이 좋다.",
                },
            },
        )
        self.assertEqual(status, 200, result)
        text = str(result.get("text") or "")
        print("\n=== NEXT IDEAS RESULT ===\n", text)
        candidates = re.findall(r"\*\*후보\s*\d+", text)
        if len(candidates) < 3:
            candidates = re.findall(r"후보\s*\d+", text)
        self.assertGreaterEqual(len(candidates), 3, text)
        self.assertLessEqual(len(candidates), 6, text)
        # At least one idea should advance the open thread (문양).
        self.assertTrue(
            ("문양" in text) or ("복선" in text) or ("은빛" in text),
            f"expected open-thread reflection in:\n{text}",
        )


if __name__ == "__main__":
    unittest.main()
