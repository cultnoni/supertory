"""Duplicate-check (dupcheck) prompt contract + live smoke cases."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


# (a) Similar action descriptions repeated inside one episode
SCENE_WITHIN = """
회의실 문이 열리자 팀장이 눈썹을 치켜떴다.
"또 늦었군." 그가 짧게 말했다.

민재는 서류를 책상 위에 올려놓았다. 창밖으로는 봄비가 내리고 있었다.
팀장이 다시 서류를 훑어보더니, 이번엔 눈썹을 찡그렸다.
"이 숫자, 어제랑 다르잖아."

민재는 대답 대신 고개를 숙였다. 팀장은 한숨을 내쉬며 창가 쪽으로 걸어갔다.
""".strip()


# (b) Current episode re-explains background already given nearby
SCENE_CURRENT = """
지하 통로에 들어서자 차가운 공기가 얼굴을 스쳤다.
나는 회귀자라서 이미 이 함정의 위치를 알고 있었다.
발소리를 죽인 채 왼쪽 벽의 틈을 더듬었다.
""".strip()

SCENE_NEIGHBOR_3 = """
처음 이곳에 왔을 때와 달랐다.
나는 회귀자라서 함정이 어디 있는지 알고 있었다.
그래서 동료들에게는 아무 말도 하지 않은 채 앞장섰다.
""".strip()

SCENE_NEIGHBOR_4 = """
횃불이 꺼지기 직전, 나는 발걸음을 멈췄다.
예전에 이 길을 걸었던 기억이 선명했다.
회귀자라서 알고 있었던 거다. 오른쪽 세 번째 타일을 밟으면 끝이다.
""".strip()


class DupcheckTests(unittest.TestCase):
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

    def test_dry_run_prompt_contract(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "dupcheck",
                "dry_run": True,
                "project_title": "중복체크",
                "main_genre": "판타지",
                "sub_genre": "현대판타지",
                "main_genre_label": "판타지",
                "sub_genre_label": "현대판타지",
                "scene_title": "5화",
                "scene_content": SCENE_WITHIN,
                "neighbor_scenes": [
                    {"index": 3, "title": "3화", "content": SCENE_NEIGHBOR_3},
                    {"index": 4, "title": "4화", "content": SCENE_NEIGHBOR_4},
                ],
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("## 중복 표현 (현재 회차 안에서)", full)
        self.assertIn("## 중복 설명 (앞뒤 4회차 이내)", full)
        self.assertIn("앞뒤 최대 4회차", full)
        self.assertNotIn("전체 위험도", full)
        self.assertNotIn("## 수정 제안", full)
        self.assertIn("반드시 고쳐야 한다고 단정하거나 지시하지 않는다", full)
        self.assertIn("짧은 관찰 코멘트", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_within_episode_expression(self) -> None:
        """(a) Similar action wording repeats inside the current episode."""
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "dupcheck",
                "project_title": "중복표현 실측",
                "main_genre": "현대소설",
                "sub_genre": "직장물",
                "main_genre_label": "현대소설",
                "sub_genre_label": "직장물",
                "scene_title": "5화",
                "scene_content": SCENE_WITHIN,
                "neighbor_scenes": [],
                "indexed_prompt": (
                    "현재 회차 안의 반복 표현과, 앞뒤 인근 회차(±4)와 겹치는 설명을 찾아 "
                    "사실과 짧은 관찰만 전달하세요. 고치라고 지시하지 마세요.\n\n"
                    f"[본문]\n{SCENE_WITHIN}"
                ),
            },
        )
        self.assertEqual(status, 200, result)
        text = result.get("text") or ""
        print("\n===== (a) 중복 표현 (현재 회차 안) =====\n", text)
        self.assertIn("중복 표현", text)
        self.assertRegex(text, r"눈썹|치켜|찡그")
        self.assertNotRegex(text, r"수정 제안|전체 위험도|반드시 고치")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_neighbor_explanation(self) -> None:
        """(b) Background already told in nearby episodes is restated."""
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "dupcheck",
                "project_title": "중복설명 실측",
                "main_genre": "판타지",
                "sub_genre": "회귀",
                "main_genre_label": "판타지",
                "sub_genre_label": "회귀",
                "scene_title": "5화",
                "scene_content": SCENE_CURRENT,
                "neighbor_scenes": [
                    {"index": 3, "title": "3화", "content": SCENE_NEIGHBOR_3},
                    {"index": 4, "title": "4화", "content": SCENE_NEIGHBOR_4},
                ],
                "indexed_prompt": (
                    "현재 회차 안의 반복 표현과, 앞뒤 인근 회차(±4)와 겹치는 설명을 찾아 "
                    "사실과 짧은 관찰만 전달하세요. 고치라고 지시하지 마세요.\n\n"
                    f"[본문]\n{SCENE_CURRENT}"
                ),
            },
        )
        self.assertEqual(status, 200, result)
        text = result.get("text") or ""
        print("\n===== (b) 중복 설명 (인근 회차) =====\n", text)
        self.assertIn("중복 설명", text)
        self.assertRegex(text, r"회귀|함정|알고")
        self.assertNotRegex(text, r"수정 제안|전체 위험도|반드시 고치")


if __name__ == "__main__":
    unittest.main(verbosity=2)
