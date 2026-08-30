"""글 다듬기 absorbs description-expand (presets + merged prompt)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parents[1]

SCENE = "등잔 연기가 코끝을 스쳤다. 멀리서 북소리가 한 번 울렸다."
SELECTED = "등잔 연기가 코끝을 스쳤다."


class RewritePresetMergeTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_ui_layout_and_menu(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('data-context-action="expand-description"', html)
        self.assertNotIn("expandDescriptionMenuItem", html)
        self.assertNotIn('value="descexpand"', html)
        self.assertNotIn('id="descExpandPanel"', html)
        self.assertIn('id="rewritePanel"', html)
        self.assertIn('id="rewriteDirectText"', html)
        self.assertIn('id="rewritePresetBlock"', html)
        self.assertIn('id="rewriteDirectionHint"', html)
        self.assertIn('data-rewrite-preset="concise"', html)
        self.assertIn('data-rewrite-preset="enrich"', html)
        self.assertIn('data-rewrite-preset="dialogue"', html)
        self.assertIn('data-rewrite-preset="psychology"', html)
        self.assertIn('data-rewrite-preset="setting"', html)
        self.assertIn("rewritePresetMoreBtn", html)
        self.assertIn('data-context-action="rewrite-text"', html)
        self.assertIn('id="rewriteTextMenuItem"', html)
        write_group = html[html.find("토리와 함께 써요"):html.find("스페셜 기능")]
        self.assertIn("글 다듬기", write_group)
        self.assertNotIn("묘사 확장", write_group)
        self.assertNotIn("문장 다듬기", html)
        self.assertIn("function openRewriteAssistModal", app_js)
        self.assertIn("openRewriteAssistModal({ fromSelection: true", app_js)
        self.assertIn("openRewriteAssistModal({ fromSelection: false", app_js)
        self.assertIn("REMOVED_AI_ASSIST_MODES", app_js)
        self.assertNotIn("expandDescriptionMenuItem", app_js)
        sentence_at = html.find('id="rewriteDirectText"')
        chips_at = html.find('id="rewritePresetBlock"')
        direction_at = html.find('id="rewriteDirectionHint"')
        self.assertTrue(0 < sentence_at < chips_at < direction_at)
        self.assertIn("REWRITE_PRESET_HINTS", app_js)
        self.assertIn("더 짧고 간결하게 다듬어 줘", app_js)
        self.assertIn("감각과 분위기를 살려서 더 풍부하게 펼쳐 줘", app_js)
        self.assertIn("서술 부분에 어울리는 캐릭터의 대사를 추가해 줘", app_js)
        self.assertIn("캐릭터의 내면 심리를 추가해 줘", app_js)
        self.assertIn("배경이나 주변 환경 묘사를 보강해 줘", app_js)
        self.assertIn("function prepareRewritePanel", app_js)
        self.assertIn("function applyRewritePreset", app_js)
        self.assertNotIn("function buildDescriptionExpandPrompt", app_js)
        self.assertNotIn("function parseDescriptionExpandDisplay", app_js)
        self.assertNotIn("async function runDescriptionExpandFromSelection", app_js)

    def test_prompt_absorbs_sensory_default_and_direction(self) -> None:
        empty = app.SuperToryHandler._build_rewrite_prompt(
            SELECTED, "문을 열고 들어왔다.", "묵연이 뒤를 이었다."
        )
        self.assertIn("[다듬을 문장]", empty)
        self.assertIn(SELECTED, empty)
        self.assertIn("지문·분위기·공간·신체 감각이 유난히 빈약해", empty)
        self.assertNotIn("[작가 요청 방향]", empty)
        self.assertNotIn("## 묘사 확장", empty)
        self.assertNotIn("1.5~2.5배", empty)
        self.assertNotIn("Core Identity", empty)

        with_dir = app.SuperToryHandler._build_rewrite_prompt(
            SELECTED,
            "문을 열고 들어왔다.",
            "묵연이 뒤를 이었다.",
            "감각과 분위기를 살려서 더 풍부하게 펼쳐 줘",
        )
        self.assertIn("[작가 요청 방향]", with_dir)
        self.assertIn("감각과 분위기를 살려서 더 풍부하게 펼쳐 줘", with_dir)
        self.assertIn("방향에 맞는 묘사·대사·심리 보강은 허용한다", with_dir)
        self.assertNotIn("지문·분위기·공간·신체 감각이 유난히 빈약해", with_dir)

    def test_legacy_descexpand_aliases_to_rewrite(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "descexpand",
                "dry_run": True,
                "project_title": "묘사",
                "main_genre": "판타지",
                "scene_content": SCENE,
                "selected_text": SELECTED,
                "expand_direction": "공간 분위기를 더",
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("등잔 연기", full)
        self.assertIn("[다듬을 문장]", full)
        self.assertIn("공간 분위기를 더", full)
        self.assertNotIn("## 묘사 확장", full)

    def test_dry_run_rewrite_with_direction(self) -> None:
        task = app.SuperToryHandler._build_rewrite_prompt(
            SELECTED, "", "", "더 짧고 간결하게 다듬어 줘"
        )
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "rewrite",
                "dry_run": True,
                "project_title": "다듬기",
                "main_genre": "판타지",
                "scene_content": SCENE,
                "selected_text": SELECTED,
                "rewrite_direction": "더 짧고 간결하게 다듬어 줘",
                "indexed_prompt": (
                    "[프로젝트 누적 정보 - 참고용]\n세계관 설정: 동양풍 무협\n\n"
                    + task
                ),
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("등잔 연기", full)
        self.assertIn("더 짧고 간결하게 다듬어 줘", full)
        self.assertIn("동양풍 무협", full)
        self.assertIn("## 다듬기 제안", full)
