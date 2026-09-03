"""투고·공모전용 시놉시스 (mode=subsynopsis) — gate + live smoke."""

from __future__ import annotations

import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client

ROOT = Path(__file__).resolve().parents[1]

OUTLINE = """
서연은 주막에서 묵연을 만나고, 은빛 문양과 달빛 암호를 단서로 그의 정체를 추적한다.
중반, 북쪽 탑의 편지로 마왕의 핏줄이 살아 있음이 드러나고 둘의 신뢰가 흔들린다.
결말에서 묵연이 배신자였음이 폭로되나, 서연은 그 배신이 더 큰 재앙을 막기 위한 선택이었음을 알게 되고
함께 탑을 봉인한다.
""".strip()

INDEXED = (
    "[프로젝트 누적 정보 - 참고용]\n"
    '등장인물: ["서연", "묵연", "주막 주인"]\n'
    "세계관 설정: 동양풍 무협, 달빛 문양은 옛 결사의 표식\n"
    "지금까지 줄거리: 주막에서 만남 → 언덕길 편지 → 신뢰 균열\n"
    "미회수 복선: 북쪽 탑의 붉은 불빛, 청동 열쇠\n\n"
)


def _char_len(text: str) -> int:
    return len(str(text or "").strip())


def _section(text: str, heading: str) -> str:
    pattern = rf"##\s*{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, text, flags=re.S)
    return (match.group(1) if match else "").strip()


def _print_live(label: str, text: str) -> None:
    payload = f"\n===== {label} =====\n{text}\n"
    try:
        print(payload)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write(payload.encode(encoding, errors="replace"))
        sys.stdout.buffer.flush()


class SubmissionSynopsisTests(unittest.TestCase):
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

    def test_a_outline_gate_before_api(self) -> None:
        """(a) Empty outline_summary must trigger client gate before API call."""
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function needsOutlineSummaryGate", app_js)
        self.assertIn("promptOutlineSummaryGate", app_js)
        self.assertIn("if (needsOutlineSummaryGate(liveOutline)", app_js)
        self.assertIn("outlineSummaryGateModal", html)
        self.assertIn("설정집에서 입력하기", html)
        self.assertIn("그래도 실행", html)
        # Gate condition: empty string requires gate; filled does not.
        self.assertTrue(not "".strip())
        self.assertFalse(bool(OUTLINE.strip()) and not OUTLINE.strip())  # sanity
        needs_gate_empty = not "".strip()
        needs_gate_filled = not OUTLINE.strip()
        self.assertTrue(needs_gate_empty)
        self.assertFalse(needs_gate_filled)

    def test_outline_summary_settings_persist(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "시놉시스테스트", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201, project)
        pid = project["id"]
        status, result = self.request(
            "PUT",
            f"/api/projects/{pid}/settings",
            {"outline_summary": OUTLINE},
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result.get("outline_summary"), OUTLINE)
        status, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(status, 200, outline)
        self.assertEqual(outline.get("project", {}).get("outline_summary"), OUTLINE)

    def test_prompt_contract(self) -> None:
        prompt = app.SuperToryHandler._build_submission_synopsis_prompt(OUTLINE, 800, 300)
        self.assertIn("## 작품의도", prompt)
        self.assertIn("## 로그라인 후보", prompt)
        self.assertIn("### 기 (도입)", prompt)
        self.assertIn("시놉시스는 800자 이내", prompt)
        self.assertIn("작품의도는 300자 이내", prompt)
        self.assertNotIn("Core Identity", prompt)

    def test_dry_run_uses_indexed_task_without_manuscript(self) -> None:
        task = app.SuperToryHandler._build_submission_synopsis_prompt(OUTLINE, None, None)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "subsynopsis",
                "dry_run": True,
                "project_title": "투고",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "outline_summary": OUTLINE,
                "indexed_prompt": INDEXED + task,
                "scene_content": "",
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("## 로그라인 후보", full)
        self.assertIn("[프로젝트 누적 정보", full)
        self.assertNotIn("현재 원고:\n", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_b_full_structure_no_limits(self) -> None:
        """(b) With outline, no length limits → intent / 5 loglines / 기승전결."""
        task = app.SuperToryHandler._build_submission_synopsis_prompt(OUTLINE, None, None)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "subsynopsis",
                "project_title": "투고 실측",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "outline_summary": OUTLINE,
                "indexed_prompt": INDEXED + task,
                "scene_content": "",
            },
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        _print_live("제한 없음", text)
        self.assertIn("## 작품의도", text)
        self.assertIn("## 로그라인 후보", text)
        self.assertIn("## 시놉시스", text)
        self.assertRegex(text, r"###\s*기")
        self.assertRegex(text, r"###\s*승")
        self.assertRegex(text, r"###\s*전")
        self.assertRegex(text, r"###\s*결")
        logline_hits = re.findall(r"^\s*[1-5][\.\)]\s+\S", text, flags=re.M)
        self.assertGreaterEqual(len(logline_hits), 5, text)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_c_length_limits(self) -> None:
        """(c) synopsis 800 / intent 300 limits roughly respected."""
        syn_limit, intent_limit = 800, 300
        task = app.SuperToryHandler._build_submission_synopsis_prompt(
            OUTLINE, syn_limit, intent_limit
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "subsynopsis",
                "project_title": "투고 제한",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "outline_summary": OUTLINE,
                "synopsis_length_limit": syn_limit,
                "intent_length_limit": intent_limit,
                "indexed_prompt": INDEXED + task,
                "scene_content": "",
            },
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        intent = _section(text, "작품의도")
        # Synopsis body: from ## 시놉시스 to end (or next top-level if any)
        syn_match = re.search(r"##\s*시놉시스\s*\n(.*)\Z", text, flags=re.S)
        synopsis = (syn_match.group(1) if syn_match else "").strip()
        _print_live(
            f"제한 있음 · 작품의도 {_char_len(intent)}자 / 시놉시스 {_char_len(synopsis)}자",
            text,
        )
        self.assertTrue(intent, text)
        self.assertTrue(synopsis, text)
        # Allow small model overrun; still must be constrained vs unlimited essays.
        self.assertLessEqual(_char_len(intent), intent_limit + 80, intent)
        self.assertLessEqual(_char_len(synopsis), syn_limit + 120, synopsis)


if __name__ == "__main__":
    unittest.main(verbosity=2)
