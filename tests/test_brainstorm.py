"""Brainstorm mode wiring checks (free expand + focused topic)."""

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
""".strip()


def _wrap_indexed(task: str, scene: str = SAMPLE_SCENE) -> str:
    return (
        "[프로젝트 누적 정보 - 참고용]\n"
        '등장인물: ["서연(현대 환생자)", "묵연(담담한 무사)"]\n'
        "세계관 설정: 동양풍 무협, 현대 문물 없음\n"
        "지금까지 줄거리: 주막에서 은빛 문양이 스친다\n"
        "미회수 복선: 소매 안 은빛 문양의 정체\n\n"
        f"{task}\n\n[본문]\n{scene}"
    )


class BrainstormTests(unittest.TestCase):
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

    def test_prompt_contract_free_and_topic(self) -> None:
        free = app.SuperToryHandler._build_brainstorm_prompt(SAMPLE_SCENE, "")
        self.assertIn("[현재 작업]", free)
        self.assertIn("자유롭게 탐색한다", free)
        self.assertIn("[층위:", free)
        self.assertIn("[브레인스토밍 결과]", free)
        self.assertNotIn("Core Identity", free)
        self.assertNotIn("6~10개", free)

        focused = app.SuperToryHandler._build_brainstorm_prompt(SAMPLE_SCENE, "새로운 조연 인물")
        self.assertIn("[작가가 지정한 주제]", focused)
        self.assertIn("새로운 조연 인물", focused)
        self.assertNotIn("자유롭게 탐색한다", focused)

    def test_dry_run_uses_indexed_task_prompt(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "브레인", "main_genre": "판타지", "sub_genre": "무협"},
        )
        self.assertEqual(status, 201)
        task = app.SuperToryHandler._build_brainstorm_prompt(SAMPLE_SCENE, "")
        indexed = _wrap_indexed(task)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "brainstorm",
                "dry_run": True,
                "project_id": project["id"],
                "project_title": "브레인",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "주막",
                "scene_content": SAMPLE_SCENE,
                "indexed_prompt": indexed,
                "user_topic": "",
            },
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("indexed_prompt_present"))
        full = result.get("full_prompt") or ""
        self.assertIn("브레인스토밍", full)
        self.assertIn("자유롭게 탐색한다", full)
        self.assertNotIn("6~10개", full)
        self.assertNotIn("실행 가능하게 적어 주세요", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_free_expand_has_varied_layers(self) -> None:
        task = app.SuperToryHandler._build_brainstorm_prompt(SAMPLE_SCENE, "")
        indexed = _wrap_indexed(task)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "brainstorm",
                "project_title": "자유 확장",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "keywords": ["동양풍", "무협"],
                "scene_title": "주막",
                "scene_content": SAMPLE_SCENE,
                "indexed_prompt": indexed,
                "user_topic": "",
                "world_setting": "동양풍 무협. 칼과 내공.",
                "character_profiles": {
                    "서연": "현대에서 환생한 주인공",
                    "묵연": "담담한 토착 무사",
                },
            },
        )
        self.assertEqual(status, 200, result)
        text = str(result.get("text") or "")
        print("\n=== BRAINSTORM FREE ===\n", text)
        ideas = re.findall(r"\*\*아이디어\s*\d+", text)
        if len(ideas) < 5:
            ideas = re.findall(r"아이디어\s*\d+", text)
        self.assertGreaterEqual(len(ideas), 5, text)
        layers = set(re.findall(r"층위:\s*(플롯|인물|세계관|주제)", text))
        self.assertGreaterEqual(len(layers), 2, f"expected diverse layers, got {layers}\n{text}")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_topic_focuses_on_supporting_cast(self) -> None:
        topic = "새로운 조연 인물"
        task = app.SuperToryHandler._build_brainstorm_prompt(SAMPLE_SCENE, topic)
        indexed = _wrap_indexed(task)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "brainstorm",
                "project_title": "주제 지정",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "keywords": ["동양풍", "무협"],
                "scene_title": "주막",
                "scene_content": SAMPLE_SCENE,
                "indexed_prompt": indexed,
                "user_topic": topic,
                "world_setting": "동양풍 무협. 칼과 내공.",
                "character_profiles": {
                    "서연": "현대에서 환생한 주인공",
                    "묵연": "담담한 토착 무사",
                },
            },
        )
        self.assertEqual(status, 200, result)
        text = str(result.get("text") or "")
        print("\n=== BRAINSTORM TOPIC ===\n", text)
        ideas = re.findall(r"\*\*아이디어\s*\d+", text)
        if len(ideas) < 5:
            ideas = re.findall(r"아이디어\s*\d+", text)
        self.assertGreaterEqual(len(ideas), 5, text)
        # Should concentrate on character / supporting-cast ideas.
        person_hits = len(re.findall(r"인물|조연|등장|새\w* 인물", text))
        self.assertGreaterEqual(person_hits, 3, text)
        self.assertIn("인물", text)


if __name__ == "__main__":
    unittest.main()
