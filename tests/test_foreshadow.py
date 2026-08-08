"""Foreshadow (복선/떡밥 탐색기) prompt contract + live smoke cases."""

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


BUILDUP_3 = [
    "은빛 문양이 손목에 스친다",
    "주막 주인이 '달빛'을 두 번 말한다",
    "칼집에 빗물이 고여 있지 않다",
]

# Registered clues: 1 reflected, 1 partial, 1 missing + unregistered detailed prop
SCENE_FORESHADOW = """
골목 끝 주막에 들어서자, 서연은 소매를 끌어올리며 은빛 문양이 손목에 스치는 감각을 느꼈다.
주막 주인이 고개를 끄덕였다. "달빛이 좋구먼." 그는 잔을 닦으며 한 번만 그렇게 말했다.

창가 쪽 탁자 위에는 낡은 청동 열쇠가 놓여 있었다. 열쇠의 손잡이에는 세 겹의 나선형 홈이
파여 있었고, 홈 사이사이마다 미세한 녹청이 끼어 있었다. 열쇠 끝은 다른 열쇠들과 달리
살짝 휘어져 있었고, 그 휘어진 면에만 유독 광택이 남아 있었다. 서연은 열쇠를 집어 들지
않은 채 시선을 거두었다.

묵연이 칼집을 점검했다. 칼집 바깥은 빗물에 젖어 있었다.
""".strip()

INDEXED_OPEN_THREAD = "북쪽 탑의 붉은 불빛"


def _assist_payload(**extra: object) -> dict:
    base = {
        "mode": "foreshadow",
        "project_title": "복선레이더",
        "main_genre": "판타지",
        "sub_genre": "무협",
        "main_genre_label": "판타지",
        "sub_genre_label": "무협",
        "scene_title": "7화",
        "scene_content": SCENE_FORESHADOW,
        "foreshadow": {
            "title": "달빛 문양",
            "target": "12장",
            "buildup": BUILDUP_3,
        },
    }
    base.update(extra)
    return base


def _index_block(open_threads: list[str], task: str, body: str) -> str:
    threads = ", ".join(open_threads) if open_threads else ""
    return (
        "[프로젝트 누적 정보 - 참고용]\n"
        '등장인물: ["서연", "묵연", "주막 주인"]\n'
        "세계관 설정: 동양풍 무협, 달빛 문양은 옛 결사의 표식\n"
        "지금까지 줄거리: 서연이 주막에 들어선다\n"
        f"미회수 복선: {threads}\n\n"
        f"{task}\n\n"
        f"[본문]\n{body}"
    )


class ForeshadowTests(unittest.TestCase):
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
            _assist_payload(dry_run=True),
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("## 등록된 단서 체크 (전체 필수)", full)
        self.assertIn("## 토리가 포착한 잠재적 복선 후보", full)
        self.assertIn("## 등록 안 된 미회수 떡밥 (인덱스 기반)", full)
        self.assertIn("## 보강 제안", full)
        self.assertIn("[최우선 원칙]", full)
        self.assertNotIn("## 단서 반영 체크리스트", full)
        self.assertNotIn("## 미회수 떡밥\n", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_a_all_registered_clues_checked(self) -> None:
        """(a) 3+ registered clues all appear in the checklist."""
        task = (
            "등록된 복선 단서를 하나도 빠짐없이 전부 체크하고, "
            "등록되지 않은 잠재 복선 후보와 인덱스의 미등록 미회수 떡밥도 알려주세요."
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            _assist_payload(
                indexed_prompt=_index_block([], task, SCENE_FORESHADOW),
            ),
        )
        self.assertEqual(status, 200, result)
        text = result.get("text") or ""
        print("\n===== (a) 등록 단서 3개 전부 =====\n", text)
        self.assertIn("등록된 단서 체크", text)
        # Each registered clue should be referenced
        self.assertRegex(text, r"은빛|문양|손목")
        self.assertRegex(text, r"달빛")
        self.assertRegex(text, r"칼집|빗물")
        # Status words should appear for checklist items (at least 3 judgments)
        judgments = re.findall(r"반영됨|부분 반영|누락", text)
        self.assertGreaterEqual(len(judgments), 3, f"expected ≥3 checklist judgments, got {judgments!r}\n{text}")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_b_unregistered_detail_candidate(self) -> None:
        """(b) Unregistered richly-described prop shows up as a candidate."""
        task = (
            "등록된 복선 단서를 하나도 빠짐없이 전부 체크하고, "
            "등록되지 않은 잠재 복선 후보와 인덱스의 미등록 미회수 떡밥도 알려주세요."
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            _assist_payload(
                indexed_prompt=_index_block([], task, SCENE_FORESHADOW),
            ),
        )
        self.assertEqual(status, 200, result)
        text = result.get("text") or ""
        print("\n===== (b) 잠재적 복선 후보 =====\n", text)
        self.assertIn("잠재적 복선 후보", text)
        self.assertRegex(text, r"열쇠|청동|나선형")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_c_index_open_thread_unregistered(self) -> None:
        """(c) Index open thread not in registered list surfaces in that section."""
        task = (
            "등록된 복선 단서를 하나도 빠짐없이 전부 체크하고, "
            "등록되지 않은 잠재 복선 후보와 인덱스의 미등록 미회수 떡밥도 알려주세요."
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            _assist_payload(
                indexed_prompt=_index_block(
                    [INDEXED_OPEN_THREAD, "은빛 문양이 손목에 스친다"],
                    task,
                    SCENE_FORESHADOW,
                ),
            ),
        )
        self.assertEqual(status, 200, result)
        text = result.get("text") or ""
        print("\n===== (c) 등록 안 된 미회수 떡밥 =====\n", text)
        self.assertIn("등록 안 된 미회수 떡밥", text)
        self.assertRegex(text, r"북쪽|탑|붉은|불빛")


if __name__ == "__main__":
    unittest.main(verbosity=2)
