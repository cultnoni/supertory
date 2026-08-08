"""Plot twist (반전 & 개연성) prompt contract + live smoke cases."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


SCENE_TWIST_SUPPORTED = """
주막 안이 조용해졌다.
묵연이 소매를 걷어 올렸다. 손목에 은빛 문양이 드러났다.
"나는 처음부터 네 편이었던 적이 없다." 그가 담담히 말했다.
서연은 주막 주인이 '달빛'을 두 번 말했던 밤을 떠올렸다.
칼집에는 빗물이 고여 있지 않았다. 처음부터 빗속을 걸은 척이었던 것이다.
""".strip()

SCENE_TWIST_UNSUPPORTED = """
문이 열리자 왕자가 웃으며 말했다.
"사실 나는 용족의 마지막 후예다. 그리고 네가 죽인 마왕은 내 아버지였다."
모두가 얼어붙었다. 서연은 아무 말도 하지 못했다.
""".strip()

BUILDUP_SUPPORTED = [
    "은빛 문양이 손목에 스친다",
    "주막 주인이 '달빛'을 두 번 말한다",
    "칼집에 빗물이 고여 있지 않다",
]

# Minimal registered foreshadow so API validation still passes (needs ≥1 buildup line)
BUILDUP_MINIMAL = [
    "창밖이 조금 어두웠다",
]


def _index_block(open_threads: list[str], timeline: str, task: str, body: str) -> str:
    threads = ", ".join(open_threads) if open_threads else ""
    return (
        "[프로젝트 누적 정보 - 참고용]\n"
        '등장인물: ["서연", "묵연"]\n'
        "세계관 설정: 동양풍 무협\n"
        f"지금까지 줄거리: {timeline}\n"
        f"미회수 복선: {threads}\n\n"
        f"{task}\n\n"
        f"[본문]\n{body}"
    )


TASK = (
    "등록된 복선 빌드업과 프로젝트 누적 정보(인덱스)를 근거로 "
    "현재 반전의 개연성을 점검하세요. 근거가 부족하면 그렇다고 명시하세요."
)


class PlotTwistTests(unittest.TestCase):
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

    def test_dry_run_keeps_instruction_and_new_system_basis(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "plottwist",
                "dry_run": True,
                "project_title": "반전검사",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "12화",
                "scene_content": SCENE_TWIST_SUPPORTED,
                "foreshadow": {
                    "title": "묵연의 정체",
                    "target": "12장",
                    "buildup": BUILDUP_SUPPORTED,
                },
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        # instruction sections unchanged
        self.assertIn("## 반전 요약", full)
        self.assertIn("## 개연성 평가", full)
        self.assertIn("## 충격도·설득력", full)
        self.assertIn("## 보강 제안 (2가지)", full)
        # dry_run returns user/full prompt (instruction+context), not system —
        # but registered buildup must still be in context
        self.assertIn("은빛 문양", full)
        self.assertIn("반전 지지용 등록 빌드업", full)
        self.assertIn("[모드: 반전 & 개연성 검사기]", full)
        self.assertIn("떡밥·복선 탐색기가 아닙니다", full)
        # Required answer sections for plottwist (not foreshadow checklist)
        self.assertRegex(full, r"다음 형식으로 답해 주세요\.\s*\n## 반전 요약")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_supported_twist_still_works(self) -> None:
        """Normal case: registered buildup supports the reveal."""
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "plottwist",
                "project_title": "반전 개연성 실측",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "12화",
                "scene_content": SCENE_TWIST_SUPPORTED,
                "foreshadow": {
                    "title": "묵연의 정체",
                    "target": "12장",
                    "buildup": BUILDUP_SUPPORTED,
                },
                "indexed_prompt": _index_block(
                    BUILDUP_SUPPORTED,
                    "주막에서 달빛 암호가 오가고, 칼집의 빗물 단서가 쌓인다 → 정체 폭로",
                    TASK,
                    SCENE_TWIST_SUPPORTED,
                ),
            },
        )
        self.assertEqual(status, 200, result)
        text = result.get("text") or ""
        print("\n===== (정상) 빌드업이 지지하는 반전 =====\n", text)
        self.assertIn("반전 요약", text)
        self.assertIn("개연성 평가", text)
        self.assertIn("충격도", text)
        self.assertIn("보강 제안", text)
        self.assertRegex(text, r"묵연|문양|달빛|칼집|배신|정체")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_insufficient_evidence_is_honest(self) -> None:
        """Extreme case: almost no foreshadow → admit insufficient evidence."""
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "plottwist",
                "project_title": "근거부족 반전",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "12화",
                "scene_content": SCENE_TWIST_UNSUPPORTED,
                "foreshadow": {
                    "title": "왕자 정체",
                    "target": "12장",
                    "buildup": BUILDUP_MINIMAL,
                },
                "indexed_prompt": _index_block(
                    [],
                    "서연이 길을 걷다",
                    TASK,
                    SCENE_TWIST_UNSUPPORTED,
                ),
            },
        )
        self.assertEqual(status, 200, result)
        text = result.get("text") or ""
        print("\n===== (극단) 근거 거의 없는 반전 =====\n", text)
        self.assertIn("반전 요약", text)
        self.assertIn("개연성 평가", text)
        self.assertRegex(
            text,
            r"근거\s*불충분|판단이\s*어렵|턱없이\s*부족|복선이\s*.{0,12}부족|"
            r"근거가\s*(부족|없|모자|희박)|빌드업.{0,12}(없|부족)|"
            r"지지\s*(할\s*)?(수\s*)?없|지지\s*정도.{0,20}부족|"
            r"개연성이\s*(부족|없다|희박)|복선\s*설계가\s*누락|연결고리가\s*전혀",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
