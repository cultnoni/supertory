"""스며듦 검사 (mode=styleblend) — continue / rewrite follow-up smoke."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client

ROOT = Path(__file__).resolve().parents[1]

LITERARY_REFERENCE = """
비 오는 골목에서 서연은 우산을 고쳐 쥐었다.
"또 늦었군." 묵연이 담담히 말했다. 칼집에서 빗물이 흘러내렸다.
서연은 대답 대신 걸음을 재촉했다. 저 멀리 주막 등불이 흔들렸다.
그녀가 전생의 카페 간판을 떠올렸다가, 이내 고개를 저었다.
""".strip()

CONTINUATION_MATCHING = """
묵연이 앞장서자 골목의 물소리가 잦아들었다.
서연은 그의 등만 바라보며, 젖은 구두코로 돌을 찼다.
주막 문이 열리자 따뜻한 기름 냄새가 먼저 나왔다.
""".strip()

CONTINUATION_MISMATCHED = """
이에 따라 서연 님께서는 상기 일정에 맞춰 주막으로의 이동을 완료해 주시기 바랍니다.
또한 본 건과 관련하여 추가적인 피드백 루프를 통해 시너지를 극대화할 수 있도록
프로액티브하게 커뮤니케이션해 주시면 감사하겠습니다. Best regards!
""".strip()

REWRITE_MISMATCHED = """
따라서 해당 문장은 다음과 같이 최적화되었습니다:
"서연은 우산을 리포지셔닝한 뒤, KPI 관점에서 지각 리스크를 인지하고 걸음 속도를 가속화했다."
""".strip()


class StyleBlendTests(unittest.TestCase):
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

    def test_ui_wiring_present(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function buildStyleBlendCheckPrompt", app_js)
        self.assertIn("function runStyleBlendCheck", app_js)
        self.assertIn('mode: "styleblend"', app_js)
        self.assertIn("styleBlendOffer", html)
        self.assertIn("rewriteStyleBlendCheckButton", html)
        self.assertIn("스며듦 검사하기", html)

    def test_prompt_contract(self) -> None:
        prompt = app.SuperToryHandler._build_style_blend_check_prompt(
            LITERARY_REFERENCE, CONTINUATION_MATCHING
        )
        self.assertIn("## 스며듦 체크 결과", prompt)
        self.assertIn("잘 어우러짐 / 약간 다르게 느껴짐 / 뚜렷하게 튐", prompt)
        self.assertNotIn("Core Identity", prompt)
        self.assertNotIn("프로젝트 누적 정보", prompt)

    def test_dry_run(self) -> None:
        task = app.SuperToryHandler._build_style_blend_check_prompt(
            LITERARY_REFERENCE, CONTINUATION_MISMATCHED
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "styleblend",
                "dry_run": True,
                "project_title": "스며듦",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "reference_text": LITERARY_REFERENCE,
                "target_text": CONTINUATION_MISMATCHED,
                "task_prompt": task,
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("스며듦 체크 결과", full)
        self.assertIn("Best regards", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_a_continue_blend(self) -> None:
        """(a) Continue-like: literary original + matching continuation."""
        task = app.SuperToryHandler._build_style_blend_check_prompt(
            LITERARY_REFERENCE, CONTINUATION_MATCHING
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "styleblend",
                "project_title": "이어쓰기 스며듦",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "reference_text": LITERARY_REFERENCE,
                "target_text": CONTINUATION_MATCHING,
                "task_prompt": task,
            },
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        print("\n===== (a) 이어서 쓰기형 스며듦 =====\n", text)
        self.assertIn("스며듦", text)
        self.assertRegex(text, r"잘 어우러짐|약간 다르게|뚜렷하게 튐")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_b_rewrite_mismatch(self) -> None:
        """(b) Rewrite-like mismatched polish should not read as fully blended."""
        task = app.SuperToryHandler._build_style_blend_check_prompt(
            LITERARY_REFERENCE, REWRITE_MISMATCHED
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "styleblend",
                "project_title": "다듬기 스며듦",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "reference_text": LITERARY_REFERENCE,
                "target_text": REWRITE_MISMATCHED,
                "task_prompt": task,
            },
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        print("\n===== (b) 문장 다듬기형 · 문체 튀는 케이스 =====\n", text)
        self.assertIn("스며듦", text)
        self.assertRegex(text, r"약간 다르게 느껴짐|뚜렷하게 튐")
        self.assertNotRegex(text, r"전반적 판단:\s*잘 어우러짐")


if __name__ == "__main__":
    unittest.main(verbosity=2)
