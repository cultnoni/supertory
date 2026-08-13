"""buildRewritePrompt branch: polish vs alternatives + plain expression isolation."""

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


def _build_rewrite_prompt_js_mirror(selected: str, before: str = "", after: str = "") -> str:
    """Mirror current web/app.js buildRewritePrompt (server has same text)."""
    return app.SuperToryHandler._build_rewrite_prompt(selected, before, after)


def _plain_prompt(text: str) -> str:
    return (
        "[현재 작업]\n"
        "아래 문장(들)에서 어색한 표현이나 부정확한 표현을 짚어 다듬으세요.\n"
        "특정 작품의 문맥이나 문체에 맞추는 작업이 아니라, 문장 자체의\n"
        "정확성과 자연스러움만 판단합니다.\n\n"
        "[판단 기준]\n"
        "1. 어색한 문장 구조나 어순을 찾는다 (번역체, 불필요한 피동/사동 표현 등).\n"
        "2. 의미가 불명확하거나 중의적으로 읽히는 표현을 찾는다.\n"
        "3. 잘못된 단어 선택이나 부정확한 어휘 사용을 찾는다.\n"
        "4. 문법적으로 어긋난 부분(조사, 어미, 호응 관계 등)을 찾는다.\n"
        "5. 특정 문체(격식체/구어체 등)로 통일하라고 요구하지 않는다. 원문의\n"
        "   문체 자체는 존중하고, 그 문체 안에서의 어색함·부정확성만 본다.\n\n"
        "[문장 규칙]\n"
        "6. 다듬은 결과만 출력한다. 설명이나 이유는 붙이지 않는다.\n"
        "7. 이미 자연스럽고 정확한 문장은 그대로 둔다 (억지로 고치지 않는다).\n\n"
        "[입력 문장]\n"
        f"{text}\n\n"
        "[다듬은 결과]"
    )


class RewriteBranchPromptTests(unittest.TestCase):
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

    def test_js_prompt_has_branch_and_ui_hooks(self) -> None:
        js = Path("web/app.js").read_text(encoding="utf-8")
        html = Path("web/index.html").read_text(encoding="utf-8")
        self.assertIn("다듬을 수 있는지 판단하세요", js)
        self.assertIn("[먼저 판단할 것 - 개선이 필요한가]", js)
        self.assertIn("## 다듬기 제안", js)
        self.assertIn("## 이미 좋은 문장이에요", js)
        self.assertIn("이 문장으로 대체하시겠어요?", js)
        self.assertIn("function parseRewriteAssistDisplay", js)
        self.assertIn("rewriteCompareAltList", js)
        self.assertIn("rewriteCompareKeepButton", html)
        self.assertIn("그대로 둘래요", html)
        self.assertIn('id="rewriteCompareAltList"', html)
        # plain expression still separate
        self.assertEqual(js.count("function buildPlainExpressionCheckPrompt"), 1)
        self.assertIn("buildPlainExpressionCheckPrompt(selectedText, directionHint)", js)
        self.assertIn("rewriteDirectionHint", html)
        self.assertIn("다듬기 방향", html)

    def test_dry_run_rewrite_prompt_includes_branch_blocks(self) -> None:
        selected = "칼집에서 물이 흘렀다."
        indexed = _build_rewrite_prompt_js_mirror(selected, "빗속이었다. ", " 서연이 보았다.")
        status, result = self.request(
            {
                "mode": "rewrite",
                "dry_run": True,
                "project_title": "분기",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "selected_text": selected,
                "scene_content": selected,
                "indexed_prompt": indexed,
            }
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("개선이 필요 없는 경우", full)
        self.assertIn("## 이미 좋은 문장이에요", full)
        self.assertIn("## 다듬기 제안", full)
        self.assertIn("이 문장으로 대체하시겠어요?", full)
        self.assertIn("빗속이었다.", full)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_a_awkward_selection_gets_polish_heading(self) -> None:
        awkward = "그는 그 방으로 들어가짐에 의해서 그 비밀이 발견되어질 수 있었다."
        indexed = _build_rewrite_prompt_js_mirror(
            awkward,
            "주막 안이 조용해졌다. ",
            " 모두가 그를 바라보았다.",
        )
        status, result = self.request(
            {
                "mode": "rewrite",
                "project_title": "분기A",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "selected_text": awkward,
                "scene_content": awkward,
                "context_before": "주막 안이 조용해졌다. ",
                "context_after": " 모두가 그를 바라보았다.",
                "indexed_prompt": indexed,
            }
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        print("\n===== (a) awkward polish =====\n", text)
        self.assertTrue(text)
        # Prefer new proposal form with reason + polished line
        has_proposal = bool(
            re.search(r"다듬기\s*제안", text)
            or re.search(r"다듬은\s*결과", text)
            or re.search(r"저는\s+", text)
        )
        self.assertTrue(has_proposal, f"expected polish proposal path, got: {text!r}")
        self.assertNotIn("들어가짐에 의해서", text)
        # Reason tone or replace question often present
        self.assertTrue(
            re.search(r"보였어요|판단했어요|대체하시겠어요|다듬은\s*결과", text),
            f"expected reason/result markers, got: {text!r}",
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_b_smooth_selection_gets_alternatives(self) -> None:
        natural = "서연은 주막 문을 밀고 들어갔다."
        indexed = _build_rewrite_prompt_js_mirror(
            natural,
            "비가 그친 뒤였다. ",
            " 안에서는 술 냄새가 났다.",
        )
        status, result = self.request(
            {
                "mode": "rewrite",
                "project_title": "분기B",
                "main_genre": "판타지",
                "sub_genre": "무협",
                "main_genre_label": "판타지",
                "sub_genre_label": "무협",
                "selected_text": natural,
                "scene_content": natural,
                "context_before": "비가 그친 뒤였다. ",
                "context_after": " 안에서는 술 냄새가 났다.",
                "indexed_prompt": indexed,
            }
        )
        self.assertEqual(status, 200, result)
        text = (result.get("text") or "").strip()
        print("\n===== (b) smooth alternatives =====\n", text)
        self.assertTrue(text)
        # Alternatives form preferred
        good = bool(
            re.search(r"이미\s*좋은\s*문장", text)
            or re.search(r"대안\s*1", text)
            or re.search(r"대안\s*2", text)
        )
        self.assertTrue(good, f"expected alternatives form, got: {text!r}")

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_c_plain_expression_still_works(self) -> None:
        awkward = "그것은 그들에 의해 수행되어질 필요가 있었다."
        status, result = self.request(
            {
                "mode": "rewrite",
                "project_title": "직접쓰기",
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
        print("\n===== (c) plain expression =====\n", text)
        self.assertTrue(text)
        self.assertNotEqual(text, awkward)
        self.assertNotIn("수행되어질", text)


if __name__ == "__main__":
    unittest.main()
