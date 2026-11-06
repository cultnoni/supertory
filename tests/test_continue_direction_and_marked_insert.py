"""이어쓰기 방향 칩·「이 장면 맞춤 제안」·AI 결과 「표시된 블록」.

- 방향 제안은 AI 모드 continue_directions(Gemini 1회). 결과는 짧은 칩 4개.
- AI 결과를 넣으면 CSS Custom Highlight로만 표시한다(본문 HTML에 표식 없음) + [되돌리기 · 확정].
설계: docs/design-ai-context-and-style-2026-11.md §4.1·§4.3.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

import app
import continue_directions

ROOT = Path(__file__).resolve().parents[1]


class ParseDirectionsTests(unittest.TestCase):
    def test_bullets_numbers_quotes_are_cleaned(self) -> None:
        text = (
            "다음 전개 후보:\n"
            "- 은서가 숨겨 둔 편지를 들킨다\n"
            "2) \"도윤이 정산서의 빈칸을 추궁한다\"\n"
            "• **헌터 길드에서 호출이 온다**\n"
            "③ 비가 그치고 장면이 아침으로 넘어간다\n"
            "- 다섯째는 버린다\n"
        )
        self.assertEqual(
            continue_directions.parse_directions(text),
            [
                "은서가 숨겨 둔 편지를 들킨다",
                "도윤이 정산서의 빈칸을 추궁한다",
                "헌터 길드에서 호출이 온다",
                "비가 그치고 장면이 아침으로 넘어간다",
            ],
        )

    def test_duplicates_and_long_lines(self) -> None:
        text = "- 같은 방향\n- 같은  방향\n- " + "가" * 100
        parsed = continue_directions.parse_directions(text)
        self.assertEqual(parsed[0], "같은 방향")
        self.assertEqual(len(parsed), 2)
        self.assertLessEqual(len(parsed[1]), continue_directions.DIRECTION_MAX_CHARS)

    def test_prompt_uses_scene_tail_only(self) -> None:
        scene = "앞부분" * 2000 + "마지막 문장이다."
        prompt = continue_directions.build_prompt(scene, user_hint="긴장감")
        self.assertIn("[이어쓰기 방향 제안]", prompt)
        self.assertIn("마지막 문장이다.", prompt)
        self.assertIn("긴장감", prompt)
        self.assertLess(len(prompt), continue_directions.SCENE_TAIL_CHARS + 1500)


class ContinueDirectionsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, project = self.request("POST", "/api/projects", {"title": "방향 검증", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        self.project_id = int(project["id"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, (json.loads(raw) if raw else {})

    def test_dry_run_builds_direction_prompt(self) -> None:
        status, data = self.request("POST", "/api/ai/assist", {
            "mode": "continue_directions",
            "project_id": self.project_id,
            "scene_content": "도윤은 정산서를 내려놓았다. 숫자 하나가 맞지 않았다.",
            "dry_run": True,
        })
        self.assertEqual(status, 200, data)
        self.assertEqual(data["mode"], "continue_directions")
        self.assertIn("[이어쓰기 방향 제안]", data["full_prompt"])
        self.assertIn("숫자 하나가 맞지 않았다", data["full_prompt"])
        # 방향 제안은 문체 블록을 넣지 않는다(글을 쓰는 요청이 아니다).
        self.assertNotIn("[작가 문체 프로필]", data["system"])

    def test_empty_scene_is_error(self) -> None:
        status, data = self.request("POST", "/api/ai/assist", {
            "mode": "continue_directions",
            "project_id": self.project_id,
            "scene_content": "",
            "dry_run": True,
        })
        self.assertGreaterEqual(status, 400, data)


class ScreenContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def _section(self, start: str, end: str) -> str:
        begin = self.app_js.index(start)
        return self.app_js[begin:self.app_js.index(end, begin)]

    def test_direction_chips_match_hint_table(self) -> None:
        chips = set(re.findall(r'data-continue-direction="([a-z]+)"', self.html))
        self.assertEqual(chips, {"tension", "monologue", "dialogue", "transition", "foreshadow", "emotion"})
        table = self._section("const CONTINUE_DIRECTION_HINTS", "};")
        for key in chips:
            self.assertIn(f"{key}:", table)
        for element_id in ("continueDirectionRow", "continueSuggestButton", "continueSuggestRow", "continueHint"):
            self.assertIn(f'id="{element_id}"', self.html)

    def test_marked_block_never_touches_manuscript_dom(self) -> None:
        section = self._section("const AI_INSERT_HIGHLIGHT", "function insertAiResultIntoEditor")
        self.assertIn("CSS.highlights.set(AI_INSERT_HIGHLIGHT", section)
        self.assertNotRegex(section, r'createElement\(\s*"mark"\s*\)')
        self.assertIn("document.body.appendChild(bar)", section)
        self.assertNotIn("editor.appendChild", section)
        # 원고에 넣는 두 경로(이어쓰기·결과 넣기, 다듬기 적용) 모두 표시한다.
        self.assertGreaterEqual(self.app_js.count("markAiInsertedNode(editor, node"), 3)

    def test_locales(self) -> None:
        keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="((?:continueDir|aiInsert)\.[^"]+)"', self.html))
        keys |= set(re.findall(r'i18n\.t\("((?:continueDir|aiInsert)\.[^"]+)"', self.app_js))
        self.assertGreater(len(keys), 15)
        for lang in ("ko", "en", "es"):
            data = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(k for k in keys if k not in data), [], lang)


if __name__ == "__main__":
    unittest.main()
