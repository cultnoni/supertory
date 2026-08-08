"""Feedback request (analyze / focused analysis) wiring checks."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


SAMPLE_SCENE = """
비 오는 골목에서 서연은 우산을 고쳐 쥐었다.
"또 늦었군." 묵연이 담담히 말했다. 칼집에서 빗물이 흘러내렸다.
서연은 대답 대신 걸음을 재촉했다. 저 멀리 주막 등불이 흔들렸다.
그녀는 전생의 카페 간판을 떠올렸다가, 이내 고개를 저었다.
묵연이 앞장서자 골목의 물소리가 잦아들었다.
""".strip()


class FocusedAnalysisTests(unittest.TestCase):
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

    def test_prompt_contract(self) -> None:
        prompt = app.SuperToryHandler._build_focused_analysis_prompt(SAMPLE_SCENE)
        self.assertIn("[현재 작업]", prompt)
        self.assertIn("편집자 관점", prompt)
        self.assertIn("독자 관점", prompt)
        self.assertIn("한 줄 총평", prompt)
        self.assertNotIn("Core Identity", prompt)

    def test_dry_run_uses_indexed_task_prompt(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "피드백", "main_genre": "판타지", "sub_genre": "무협"},
        )
        self.assertEqual(status, 201)
        indexed = (
            "[프로젝트 누적 정보 - 참고용]\n"
            '등장인물: ["서연", "묵연"]\n'
            "세계관 설정: 동양풍 무협\n\n"
            + app.SuperToryHandler._build_focused_analysis_prompt(SAMPLE_SCENE)
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "analyze",
                "dry_run": True,
                "project_id": project["id"],
                "project_title": "피드백",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "1화",
                "scene_content": SAMPLE_SCENE,
                "indexed_prompt": indexed,
                "focus_scene_only": True,
            },
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("indexed_prompt_present"))
        full = result.get("full_prompt") or ""
        self.assertIn("편집자 관점", full)
        self.assertIn("독자 관점", full)
        self.assertIn("한 줄 총평", full)
        self.assertNotIn("① 강점 ② 아쉬운 점", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_feedback_splits_editor_and_reader(self) -> None:
        indexed = (
            "[프로젝트 누적 정보 - 참고용]\n"
            '등장인물: ["서연", "묵연"]\n'
            "세계관 설정: 동양풍 무협, 서연은 현대 환생자\n"
            "지금까지 줄거리: 두 사람이 비 오는 골목을 지난다\n"
            "미회수 복선: \n\n"
            + app.SuperToryHandler._build_focused_analysis_prompt(SAMPLE_SCENE)
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "analyze",
                "project_title": "피드백 실측",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "keywords": ["동양풍", "무협"],
                "scene_title": "비 골목",
                "scene_content": SAMPLE_SCENE,
                "indexed_prompt": indexed,
                "focus_scene_only": True,
                "world_setting": "동양풍 무협. 칼과 내공. 현대 문물은 없다.",
                "character_profiles": {
                    "서연": "현대에서 환생한 주인공",
                    "묵연": "담담한 토착 무사",
                },
            },
        )
        self.assertEqual(status, 200, result)
        text = str(result.get("text") or "")
        print("\n=== FEEDBACK RESULT ===\n", text)
        self.assertIn("편집자", text)
        self.assertIn("독자", text)
        self.assertTrue("좋은 점" in text or "개선점" in text)
        self.assertGreater(len(text), 200)
