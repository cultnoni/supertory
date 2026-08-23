"""Direct-write expression check vs manuscript rewrite separation."""

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

# Mirror web/app.js buildPlainExpressionCheckPrompt / buildRewritePrompt markers
PLAIN_MARKERS = [
    "문장 자체의",
    "정확성과 자연스러움만 판단합니다",
    "번역체",
    "[입력 문장]",
    "## 다듬기 제안",
    "이 문장으로 대체하시겠어요?",
]
REWRITE_MARKERS = [
    "선택된 문장(또는 문단)을 더 나은 문장으로 다듬을 수 있는지 판단하세요",
    "[앞뒤 맥락 - 참고용, 다듬지 않음]",
    "[다듬을 문장]",
]


def _plain_prompt(text: str) -> str:
    # Mirror buildPlainExpressionCheckPrompt (reason + question on needs-fix)
    return (
        "[현재 작업]\n"
        "아래 문장(들)에서 어색한 표현이나 부정확한 표현을 짚어 다듬을 수 있는지 판단하세요.\n"
        "특정 작품의 문맥이나 문체에 맞추는 작업이 아니라, 문장 자체의\n"
        "정확성과 자연스러움만 판단합니다.\n\n"
        "[판단 기준]\n"
        "1. 어색한 문장 구조나 어순을 찾는다 (번역체, 불필요한 피동/사동 표현 등).\n"
        "2. 의미가 불명확하거나 중의적으로 읽히는 표현을 찾는다.\n"
        "3. 잘못된 단어 선택이나 부정확한 어휘 사용을 찾는다.\n"
        "4. 문법적으로 어긋난 부분(조사, 어미, 호응 관계 등)을 찾는다.\n"
        "5. 특정 문체(격식체/구어체 등)로 통일하라고 요구하지 않는다. 원문의\n"
        "   문체 자체는 존중하고, 그 문체 안에서의 어색함·부정확성만 본다.\n\n"
        "[먼저 판단할 것 - 개선이 필요한가]\n"
        "문장에 실제로 위 기준에 해당하는 부분이 있는지 먼저 판단한다.\n"
        "이미 자연스럽고 정확한 문장이라면, 있지도 않은 문제를 억지로 만들어 고치지 않는다.\n\n"
        "[개선이 필요한 경우 - 이유 설명 + 다듬은 결과]\n"
        "왜 다듬는 게 좋다고 판단했는지 1~2문장으로 짧게 설명한다.\n"
        "그다음 다듬은 결과를 제시하고, 작가의 생각을 묻는다.\n\n"
        "[출력 형식]\n"
        "개선이 필요한 경우:\n"
        "## 다듬기 제안\n"
        "저는 (이유)로 다듬기가 필요해 보였어요.\n\n"
        "**다듬은 결과:** (다듬어진 문장)\n\n"
        "작가님의 생각은 어떤가요? 이 문장으로 대체하시겠어요?\n\n"
        "[입력 문장]\n"
        f"{text}\n\n"
        "[결과]"
    )


class PlainExpressionCheckTests(unittest.TestCase):
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

    def request(self, payload: dict) -> tuple[int, object]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=180
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection.request(
            "POST", "/api/ai/assist", body, {"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_client_js_defines_plain_prompt_and_branches(self) -> None:
        js = Path("web/app.js").read_text(encoding="utf-8")
        self.assertIn("function buildPlainExpressionCheckPrompt(inputText, directionHint = \"\", clusterId)", js)
        self.assertIn("function buildRewritePrompt(selectedText, contextBefore = \"\", contextAfter = \"\", directionHint = \"\", clusterId)", js)
        # Direct path must call plain prompt, not only buildRewritePrompt
        self.assertIn("buildPlainExpressionCheckPrompt(selectedText, directionHint)", js)
        self.assertIn('sourceMode === "direct"', js)
        # Must skip index attach for direct
        self.assertIn("skip_project_index", js)
        # attachIndexedPrompt for rewrite only on non-direct branch remains
        self.assertIn('attachIndexedPromptToAssistBody(body, "rewrite", selectedText)', js)

    def test_dry_run_direct_uses_plain_prompt_not_context_rewrite(self) -> None:
        awkward = "그는 그 방에 들어가짐에 의해서 발견되어질 수 있었다."
        plain = _plain_prompt(awkward)
        status, result = self.request(
            {
                "mode": "rewrite",
                "dry_run": True,
                "project_title": "표현점검",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "selected_text": awkward,
                "scene_content": awkward,
                "context_before": "",
                "context_after": "",
                "indexed_prompt": plain,
                "project_index": None,
            }
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        for m in PLAIN_MARKERS:
            self.assertIn(m, full)
        for m in REWRITE_MARKERS:
            self.assertNotIn(m, full)
        self.assertIn(awkward, full)
        self.assertFalse(result.get("indexed_prompt_has_index_block"))

    def test_dry_run_selection_still_uses_context_rewrite_when_indexed(self) -> None:
        selected = "칼집에서 빗물이 흘렀다."
        before = "주막 문이 열렸다. "
        after = " 서연이 미간을 찌푸렸다."
        indexed = app.SuperToryHandler._build_rewrite_prompt(selected, before, after)
        status, result = self.request(
            {
                "mode": "rewrite",
                "dry_run": True,
                "project_title": "문맥다듬기",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "selected_text": selected,
                "scene_content": selected,
                "context_before": before,
                "context_after": after,
                "indexed_prompt": indexed,
            }
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        for m in REWRITE_MARKERS:
            self.assertIn(m, full)
        self.assertIn(before.strip(), full)
        self.assertIn(after.strip(), full)
        # Must not be plain-expression-only framing
        self.assertNotIn("정확성과 자연스러움만 판단합니다", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_a_awkward_translationese_is_polished(self) -> None:
        awkward = "그는 그 방으로 들어가짐에 의해서 그 비밀이 발견되어질 수 있었다."
        status, result = self.request(
            {
                "mode": "rewrite",
                "project_title": "표현점검",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "selected_text": awkward,
                "scene_content": awkward,
                "indexed_prompt": _plain_prompt(awkward),
                "project_index": None,
            }
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        print("\n===== (a) awkward → polished =====\n", text)
        self.assertTrue(text)
        self.assertNotIn("들어가짐에 의해서", text)
        self.assertNotIn("발견되어질", text)
        self.assertTrue(
            re.search(r"다듬기\s*제안|다듬은\s*결과|저는\s+", text)
            or text != awkward,
            f"expected polish, got: {text!r}",
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_b_natural_sentence_kept(self) -> None:
        natural = "서연은 주막 문을 밀고 들어갔다."
        status, result = self.request(
            {
                "mode": "rewrite",
                "project_title": "표현점검",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "selected_text": natural,
                "scene_content": natural,
                "indexed_prompt": _plain_prompt(natural),
                "project_index": None,
            }
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        print("\n===== (b) natural kept =====\n", text)
        self.assertTrue(text)
        self.assertRegex(text, r"서연")
        self.assertRegex(text, r"주막")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_c_selection_rewrite_still_context_aware(self) -> None:
        selected = "칼집에서 물이 흘렀다."
        before = "빗속에서도 그의 칼집은 말라 있어야 했다. 그런데 "
        after = " 서연은 그 단서를 놓치지 않았다."
        indexed = app.SuperToryHandler._build_rewrite_prompt(selected, before, after)
        status, result = self.request(
            {
                "mode": "rewrite",
                "project_title": "문맥다듬기",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "selected_text": selected,
                "scene_content": selected,
                "context_before": before,
                "context_after": after,
                "indexed_prompt": indexed,
            }
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        print("\n===== (c) context rewrite =====\n", text)
        self.assertTrue(text)
        self.assertTrue(
            re.search(r"칼집|물|빗", text),
            f"expected scabbard/water cue retained, got: {text!r}",
        )


if __name__ == "__main__":
    unittest.main()
