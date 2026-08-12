"""세계관 묘사 도우미 (mode=worlddesc)."""

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

ROOT = Path(__file__).resolve().parents[1]

SCENE = """
서연은 돌계단을 오르며 소매를 고쳐 쥐었다.
묵연이 앞장섰다. "문은 왼쪽이다." 그의 목소리는 낮았다.
등잔 연기가 코끝을 스쳤고, 멀리서 북소리가 한 번 울렸다.
""".strip()

INDEXED_PREFIX = (
    "[프로젝트 누적 정보 - 참고용]\n"
    '등장인물: ["서연", "묵연"]\n'
    "세계관 설정: 동양풍 무협, 달빛 결사, 왕궁은 흑단목과 청동으로 지어짐, "
    "내정에는 향로와 붉은 기둥, 서양식 성은 존재하지 않음\n"
    "지금까지 줄거리: 주막 → 언덕길 → 왕궁 입성\n"
    "미회수 복선: 북쪽 탑의 붉은 불빛\n\n"
)


class WorldDescriptionTests(unittest.TestCase):
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

    def test_ui_and_prompt_wiring(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function buildWorldDescriptionPrompt", app_js)
        self.assertIn('"worlddesc"', app_js)
        self.assertIn("getWorldDescSubject", app_js)
        self.assertIn('value="worlddesc"', html)
        self.assertIn("worldDescSubject", html)
        self.assertIn("세계관 묘사", html)
        self.assertIn('value="worlddesc"', html)
        self.assertIn("토리와 함께 써요", html)
        self.assertIn("토리와 구상해요", html)
        self.assertIn("토리와 확인해요", html)
        self.assertIn("떡밥·복선 탐색기", html)

    def test_prompt_contract(self) -> None:
        prompt = app.SuperToryHandler._build_world_description_prompt("왕궁 내부 묘사", SCENE)
        self.assertIn("[묘사 대상]", prompt)
        self.assertIn("왕궁 내부 묘사", prompt)
        self.assertIn("**버전 1**", prompt)
        self.assertNotIn("Core Identity", prompt)

    def test_requires_subject(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worlddesc",
                "dry_run": True,
                "project_title": "묘사",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_content": SCENE,
                "target_subject": "",
            },
        )
        self.assertEqual(status, 400, result)
        self.assertIn("묘사 대상", str(result.get("error") or ""))

    def test_dry_run_indexed(self) -> None:
        task = app.SuperToryHandler._build_world_description_prompt("왕궁 내부 묘사", SCENE)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worlddesc",
                "dry_run": True,
                "project_title": "묘사",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_content": SCENE,
                "target_subject": "왕궁 내부 묘사",
                "indexed_prompt": INDEXED_PREFIX + task + f"\n\n[본문]\n{SCENE}",
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("왕궁 내부 묘사", full)
        self.assertIn("[프로젝트 누적 정보", full)
        self.assertIn("흑단목", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_palace_description(self) -> None:
        """Fantasy eastern setting: 왕궁 내부 묘사 → 2–3 angled versions."""
        task = app.SuperToryHandler._build_world_description_prompt("왕궁 내부 묘사", SCENE)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worlddesc",
                "project_title": "동양풍 무협",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "keywords": ["동양풍", "무협", "왕궁"],
                "scene_content": SCENE,
                "target_subject": "왕궁 내부 묘사",
                "indexed_prompt": INDEXED_PREFIX + task + f"\n\n[본문]\n{SCENE}",
            },
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        print("\n===== 세계관 묘사 · 왕궁 내부 =====\n", text)
        versions = re.findall(r"\*\*버전\s*[123]\*\*", text)
        self.assertGreaterEqual(len(versions), 2, text)
        # Different emphasis markers
        emphasis = re.findall(r"강조점\s*:\s*([^\]]+)", text)
        self.assertGreaterEqual(len(emphasis), 2, text)
        # Reflect eastern / established world (not western castle cliché only)
        self.assertRegex(text, r"흑단|청동|기둥|향|등잔|돌|대청|전각|붉은|동양|무협|결사")
        # Should not invent western castle as primary frame when index forbids it
        # (allow incidental words; fail only if clearly western castle framing dominates)
        western_hits = len(re.findall(r"고딕|성채|스테인드글라스|나이트|드레이크", text))
        self.assertLessEqual(western_hits, 1, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
