"""원고 찾아 바꾸기: 일치 로직(node) + 화면 계약.

- 하이라이트는 CSS Custom Highlight(::highlight)로 칠하고 본문 DOM에 <mark>를 넣지 않는다.
- 바꾸기는 편집기 입력 경로(execCommand)로 해서 Ctrl+Z·자동 저장이 그대로 동작한다.
- 모두 바꾸기 직전 원고는 이전 버전에 ★(before_replace_all)로 남긴다.
설계: docs/design-ai-context-and-style-2026-11.md §7-2.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "index.html"
APP_JS = ROOT / "web" / "app.js"
NODE_TEST = ROOT / "tests" / "test_find_replace_model.js"


def _find_section(app_js: str) -> str:
    start = app_js.index("/* —— 원고 찾아 바꾸기")
    end = app_js.index("/* —— 회차 「이전 버전」", start)
    return app_js[start:end]


class FindReplaceTests(unittest.TestCase):
    def test_node_match_logic(self) -> None:
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            raise unittest.SkipTest("node 없음")
        result = subprocess.run(
            [node, str(NODE_TEST)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + "\n" + result.stderr)

    def test_find_bar_controls_exist(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        for element_id in (
            "findBar", "findInput", "findCount", "findPrevButton", "findNextButton",
            "findCloseButton", "findReplaceToggle", "findReplaceRow", "replaceInput",
            "findReplaceOneButton", "findReplaceAllButton", "findAdvancedToggle",
            "findAdvancedRow", "findError",
        ):
            self.assertIn(f'id="{element_id}"', html, element_id)
        for option in ("caseSensitive", "selectionOnly", "includeNotes", "regex"):
            self.assertIn(f'data-find-option="{option}"', html, option)
        # 모델 스크립트는 app.js보다 먼저 싣는다.
        self.assertLess(html.index("/find_replace_model.js"), html.index('src="/app.js'))

    def test_highlights_do_not_touch_the_manuscript_dom(self) -> None:
        section = _find_section(APP_JS.read_text(encoding="utf-8"))
        self.assertIn("CSS.highlights.set", section)
        self.assertNotRegex(section, r'createElement\(\s*"mark"\s*\)')
        self.assertNotIn('className = "find-hit"', section)

    def test_replace_uses_editor_input_path_and_checkpoint(self) -> None:
        section = _find_section(APP_JS.read_text(encoding="utf-8"))
        self.assertIn('execCommand("insertText"', section)
        self.assertIn('checkpointSceneBefore("before_replace_all")', section)
        # 잠긴(완성) 회차는 바꾸지 않는다.
        self.assertIn("isContentEditable", section)

    def test_locale_keys_in_every_language(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        section = _find_section(APP_JS.read_text(encoding="utf-8"))
        keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="(find\.[^"]+)"', html))
        keys |= set(re.findall(r'i18n\.t\("(find\.[^"]+)"', section))
        self.assertGreater(len(keys), 10)
        for lang in ("ko", "en", "es"):
            data = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            missing = sorted(key for key in keys if key not in data)
            self.assertEqual(missing, [], f"{lang}.json 누락")


if __name__ == "__main__":
    unittest.main()
