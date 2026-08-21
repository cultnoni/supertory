"""Manuscript description expand (mode=descexpand) from selection context menu."""

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


class DescriptionExpandTests(unittest.TestCase):
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

    def test_ui_and_prompt_wiring(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function buildDescriptionExpandPrompt", app_js)
        self.assertIn("function parseDescriptionExpandDisplay", app_js)
        self.assertIn("async function runDescriptionExpandFromSelection", app_js)
        self.assertIn('"descexpand"', app_js)
        self.assertIn('data-context-action="expand-description"', html)
        self.assertIn("expandDescriptionMenuItem", html)
        self.assertIn("묘사 확장", html)
        self.assertIn('value="descexpand"', html)
        self.assertIn('id="descExpandPanel"', html)
        rewrite_opt = html.find('value="rewrite"')
        expand_opt = html.find('value="descexpand"')
        world_opt = html.find('value="worlddesc"')
        self.assertTrue(0 < rewrite_opt < expand_opt < world_opt)

    def test_prompt_contract(self) -> None:
        prompt = app.SuperToryHandler._build_description_expand_prompt(
            SELECTED, "문을 열고 들어왔다.", "묵연이 뒤를 이었다."
        )
        self.assertIn("[선택 원문]", prompt)
        self.assertIn(SELECTED, prompt)
        self.assertIn("[앞 문맥]", prompt)
        self.assertIn("**확장 결과:**", prompt)
        self.assertNotIn("Core Identity", prompt)
        self.assertIn("1.5~2.5배", prompt)
        self.assertNotIn("[작가 요청 방향]", prompt)

        with_dir = app.SuperToryHandler._build_description_expand_prompt(
            SELECTED, "문을 열고 들어왔다.", "묵연이 뒤를 이었다.", "공간 분위기를 더"
        )
        self.assertIn("[작가 요청 방향]", with_dir)
        self.assertIn("공간 분위기를 더", with_dir)

    def test_requires_selected_text(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "descexpand",
                "dry_run": True,
                "project_title": "묘사",
                "main_genre": "판타지",
                "scene_content": "",
                "selected_text": "",
            },
        )
        self.assertEqual(status, 400, result)
        self.assertTrue(
            "펼칠" in str(result.get("error") or "") or "원고" in str(result.get("error") or ""),
            result,
        )

    def test_dry_run_indexed(self) -> None:
        task = app.SuperToryHandler._build_description_expand_prompt(SELECTED)
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
                "indexed_prompt": (
                    "[프로젝트 누적 정보 - 참고용]\n세계관 설정: 동양풍 무협\n\n"
                    + task
                ),
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("등잔 연기", full)
        self.assertIn("묘사 확장", full)
        self.assertIn("동양풍 무협", full)
