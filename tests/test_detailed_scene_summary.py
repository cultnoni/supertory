"""Detailed scene summary (mode=summarize) vs one-line binder summary."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


SCENE_SHORT = """
비 오는 골목에서 서연은 우산을 고쳐 쥐었다.
"또 늦었군." 묵연이 담담히 말했다. 칼집에서 빗물이 흘러내렸다.
서연은 대답 대신 걸음을 재촉했다. 저 멀리 주막 등불이 흔들렸다.
""".strip()


SCENE_LONG = """
새벽 안개가 걷히기 전, 서연은 주막 뒷마당에서 묵연과 마주쳤다.
묵연은 소매를 걷어 은빛 문양을 잠시 보여주었다가 다시 가렸다.
"달빛이 짙은 밤엔 말하지 마." 주막 주인이 안쪽에서 두 번 속삭였다.

낮이 되자 두 사람은 북쪽 언덕길을 올랐다. 서연은 어제 주막에서 본 청동 열쇠를
떠올리며 말이 줄었다. 묵연은 칼집을 점검했다. 바깥은 젖어 있었지만 안쪽은 말랐다.

오후, 언덕 위에서 낯선 전령이 말을 멈추고 서연에게 봉인된 편지를 건넸다.
봉인에는 북쪽 탑의 문양이 찍혀 있었다. 서연이 편지를 열자 단 한 줄이었다.
「네가 죽인 마왕의 핏줄이 아직 살아 있다.」

해가 기울 무렵 묵연이 먼저 입을 열었다.
"그 편지, 누구에게도 보이지 마." 서연은 고개를 끄덕였지만, 손끝은 떨리고 있었다.
밤이 되자 주막으로 돌아온 두 사람 사이에는 어제의 가벼운 침묵과 다른 거리가 생겼다.
서연은 창가의 열쇠를 다시 찾아보았으나 탁자는 이미 비어 있었다.
""".strip()


def _char_len(text: str) -> int:
    return len(str(text or "").strip())


class DetailedSceneSummaryTests(unittest.TestCase):
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

    def test_prompt_contract_no_index_no_core_identity(self) -> None:
        prompt = app.SuperToryHandler._build_detailed_scene_summary_prompt(SCENE_SHORT)
        self.assertIn("300~500자", prompt)
        self.assertIn("바인더 캡션용 짧은 요약이 아니라", prompt)
        self.assertNotIn("Core Identity", prompt)
        self.assertNotIn("프로젝트 누적 정보", prompt)
        self.assertNotIn("event_summary", prompt)

    def test_dry_run_uses_task_prompt_without_index(self) -> None:
        task = app.SuperToryHandler._build_detailed_scene_summary_prompt(SCENE_SHORT)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "summarize",
                "dry_run": True,
                "project_title": "씬요약",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "1화",
                "scene_content": SCENE_SHORT,
                "task_prompt": task,
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("300~500자", full)
        self.assertNotIn("[프로젝트 누적 정보", full)
        self.assertNotIn("3~6문장으로 요약", full)
        # Body embedded once in task — not duplicated as "현재 원고:"
        self.assertNotIn("현재 원고:\n", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_short_vs_one_line(self) -> None:
        """(a) Short episode: detailed summary longer than binder one-liner."""
        detailed_prompt = app.SuperToryHandler._build_detailed_scene_summary_prompt(SCENE_SHORT)
        status_d, result_d = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "summarize",
                "project_title": "짧은회차",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "1화",
                "scene_content": SCENE_SHORT,
                "task_prompt": detailed_prompt,
            },
        )
        self.assertEqual(status_d, 200, result_d)
        detailed = (result_d.get("text") or "").strip()

        # One-line binder summary via free + buildSummaryPrompt-style task
        one_line_prompt = (
            "[현재 작업]\n"
            f"아래 본문을 60자 내외(최대 75자를 넘지 않도록)로 요약하세요.\n\n"
            "[판단 기준]\n"
            "1. 핵심만 압축한다.\n"
            "4. 본문에 없는 내용을 추측하거나 덧붙이지 않는다.\n\n"
            "[문장 규칙]\n"
            "9. 완성된 요약문만 출력한다.\n\n"
            f"[본문]\n{SCENE_SHORT}\n\n[요약]"
        )
        status_o, result_o = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "free",
                "project_title": "짧은회차",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "1화",
                "scene_content": "",
                "prompt": one_line_prompt,
                "user_prompt": one_line_prompt,
                "user_request": one_line_prompt,
            },
        )
        self.assertEqual(status_o, 200, result_o)
        one_line = (result_o.get("text") or "").strip()

        print(f"\n===== (a) 짧은 회차 · 한 줄 요약 ({_char_len(one_line)}자) =====\n{one_line}")
        print(f"\n===== (a) 짧은 회차 · 회차 요약 ({_char_len(detailed)}자) =====\n{detailed}")

        self.assertGreater(_char_len(detailed), _char_len(one_line))
        # Short source: do not force 300자 — prompt says don't pad short episodes.
        self.assertGreaterEqual(_char_len(detailed), 90)
        self.assertLessEqual(_char_len(detailed), 500)
        self.assertLessEqual(_char_len(one_line), 160)
        self.assertNotEqual(detailed, one_line)
        # Detailed should carry more beats than the caption
        self.assertRegex(detailed, r"우산|칼집|빗물|등불")
        self.assertRegex(detailed, r"서연|묵연")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_long_multi_event(self) -> None:
        """(b) Long multi-event episode stays roughly in 300–500 range and covers flow."""
        detailed_prompt = app.SuperToryHandler._build_detailed_scene_summary_prompt(SCENE_LONG)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "summarize",
                "project_title": "긴회차",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "scene_title": "7화",
                "scene_content": SCENE_LONG,
                "task_prompt": detailed_prompt,
            },
        )
        self.assertEqual(status, 200, result)
        detailed = (result.get("text") or "").strip()
        print(f"\n===== (b) 긴 회차 · 회차 요약 ({_char_len(detailed)}자) =====\n{detailed}")

        self.assertGreaterEqual(_char_len(detailed), 250)
        self.assertLessEqual(_char_len(detailed), 700)
        # Multiple beats should surface
        hits = sum(
            1
            for token in ("문양", "달빛", "편지", "열쇠", "묵연", "서연", "마왕")
            if token in detailed
        )
        self.assertGreaterEqual(hits, 3, f"expected multi-event coverage, got:\n{detailed}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
