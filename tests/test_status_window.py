"""게임물 상태창: 프리셋 정리·저장 API·「텍스트 안전」 내보내기·화면 계약 (임시 DB).

설계: docs/design-ai-context-and-style-2026-11.md §13.
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import app
import status_window

ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "test_status_window_model.js"

BOX_BLOCK = (
    '<div class="st-status-window" data-status-window="tpl_system" data-sw-frame="box" '
    'data-sw-export="text_safe"><div>┌─────────</div><div>│ 시스템</div><div>├─────────</div>'
    "<div>│ 레벨: 3</div><div>│ 골드: 1,200</div><div>└─────────</div></div>"
)
KEEP_BLOCK = (
    '<div class="st-status-window" data-status-window="tpl_basic" data-sw-frame="divider" '
    'data-sw-export="keep"><div>━━━━━━━━</div><div>[상태창]</div><div>이름: 진우</div><div>━━━━━━━━</div></div>'
)


class StatusWindowModuleTests(unittest.TestCase):
    def test_normalize_presets(self) -> None:
        presets = status_window.normalize_presets([
            {"id": "a", "name": "헌터", "frame": "box", "items": [{"name": "레벨", "type": "number"}, {"name": ""}]},
            {"id": "a", "name": "겹친 id", "frame": "zigzag", "label_style": "?", "export_mode": "text_safe"},
            "깨진 값",
            {"id": "나쁜 id!", "title": "제목만"},
        ])
        self.assertEqual([p["id"] for p in presets], ["a", "a_2", "p4"])
        self.assertEqual(presets[0]["items"], [{"name": "레벨", "group": "", "default": "", "type": "number"}])
        self.assertEqual(presets[1]["frame"], "divider")
        self.assertEqual(presets[1]["label_style"], "colon")
        self.assertEqual(presets[1]["export_mode"], "text_safe")
        self.assertEqual(presets[2]["name"], "제목만")
        self.assertEqual(status_window.normalize_presets("{깨짐"), [])
        self.assertEqual(status_window.normalize_presets({"a": 1}), [])

    def test_limits(self) -> None:
        many = [{"id": f"p{i}", "name": "x" * 99, "items": [{"name": f"항목{j}"} for j in range(60)]} for i in range(40)]
        presets = status_window.normalize_presets(many)
        self.assertEqual(len(presets), status_window.MAX_PRESETS)
        self.assertEqual(len(presets[0]["items"]), status_window.MAX_ITEMS)
        self.assertEqual(len(presets[0]["name"]), status_window.MAX_NAME)

    def test_text_safe_html_only_touches_text_safe_blocks(self) -> None:
        html = f"<div>앞 문단</div>{BOX_BLOCK}{KEEP_BLOCK}<div>뒤 문단</div>"
        out = status_window.to_text_safe_html(html)
        plain = app.plain_text_from_content(out)
        self.assertNotIn("┌", plain)
        self.assertNotIn("│", plain)
        self.assertIn("시스템\n레벨: 3\n골드: 1,200", plain)
        # 「모양 유지」 블록은 그대로
        self.assertIn("━━━━━━━━\n[상태창]\n이름: 진우", plain)
        self.assertIn("앞 문단", plain)
        self.assertIn("뒤 문단", plain)
        self.assertEqual(status_window.to_text_safe_html("<p>상태창 없음</p>"), "<p>상태창 없음</p>")

    def test_text_safe_escapes_and_keeps_unclosed_markup(self) -> None:
        block = '<div data-status-window="x" data-sw-export="text_safe"><div>이름: &lt;b&gt;진우</div></div>'
        out = status_window.to_text_safe_html(block)
        self.assertIn("&lt;b&gt;진우", out)
        broken = '<div data-status-window="x" data-sw-export="text_safe"><div>닫히지 않음'
        self.assertEqual(status_window.to_text_safe_html(broken), broken)

    def test_plain_text_counts_status_window(self) -> None:
        # 상태창은 독자가 보는 글: 기본 글자 수·내보내기에 들어간다(주석 단락과 반대).
        plain = app.plain_text_from_content(f"<div>본문</div>{KEEP_BLOCK}")
        self.assertIn("이름: 진우", plain)
        self.assertIn("이름: 진우", app.export_plain_text_from_content(KEEP_BLOCK))

    def test_python_and_js_text_safe_rules_match(self) -> None:
        js = (ROOT / "web" / "status_window_model.js").read_text(encoding="utf-8")
        py_chars = status_window._FRAME_ONLY_RE.pattern
        js_match = re.search(r"const FRAME_ONLY_RE = /(.+)/;", js)
        self.assertIsNotNone(js_match)
        self.assertEqual(js_match.group(1), py_chars)


class StatusWindowApiTests(unittest.TestCase):
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
        status, project = self.request("POST", "/api/projects", {"title": "상태창 검증", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        self.project_id = int(project["id"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def raw(self, method: str, path: str, payload: dict | None = None) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        status, data = self.raw(method, path, payload)
        return status, (json.loads(data.decode("utf-8")) if data else {})

    def test_migration_column_and_version(self) -> None:
        with app.database() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(project)")}
            self.assertIn("status_window_presets_json", columns)
            version = connection.execute("SELECT 1 FROM schema_migration WHERE version = 109").fetchone()
            self.assertIsNotNone(version)
        # 다시 초기화해도 깨지지 않는다(멱등).
        app.initialise_database()

    def test_get_empty_save_and_reload(self) -> None:
        path = f"/api/projects/{self.project_id}/status-window-presets"
        status, data = self.request("GET", path)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["presets"], [])
        status, data = self.request("POST", path, {"presets": [
            {"id": "hero", "name": "주인공", "title": "상태창", "frame": "bracket",
             "items": [{"name": "레벨", "default": "1", "type": "number"}],
             "display": {"background": "soft", "font": "mono"}, "export_mode": "text_safe"},
        ]})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["presets"][0]["display"], {"background": "soft", "font": "mono"})
        status, data = self.request("GET", path)
        self.assertEqual([p["id"] for p in data["presets"]], ["hero"])
        self.assertEqual(data["presets"][0]["frame"], "bracket")
        # 통째로 바꿔 저장(삭제)
        status, data = self.request("POST", path, {"presets": []})
        self.assertEqual(data["presets"], [])

    def test_bad_requests(self) -> None:
        status, _ = self.request("POST", f"/api/projects/{self.project_id}/status-window-presets", {"presets": "x"})
        self.assertGreaterEqual(status, 400)
        status, _ = self.request("GET", "/api/projects/99999/status-window-presets")
        self.assertGreaterEqual(status, 400)

    def test_txt_export_applies_text_safe(self) -> None:
        status, chapter = self.request("POST", f"/api/projects/{self.project_id}/chapters", {"title": "1장"})
        self.assertEqual(status, 201, chapter)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "회차1"})
        self.assertEqual(status, 201, scene)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        status, _ = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "content_md": f"<div>시스템 창이 떴다.</div>{BOX_BLOCK}{KEEP_BLOCK}",
            "title": "회차1",
            "row_version": detail["row_version"],
        })
        self.assertEqual(status, 200)
        status, data = self.raw("POST", f"/api/projects/{self.project_id}/export",
                                {"format": "txt", "save_to_folder": False})
        self.assertEqual(status, 200, data[:200])
        text = data.decode("utf-8-sig")
        self.assertIn("시스템 창이 떴다.", text)
        self.assertIn("레벨: 3", text)
        self.assertNotIn("│ 레벨", text)
        self.assertNotIn("┌", text)
        self.assertIn("[상태창]", text)  # 모양 유지 블록
        self.assertIn("━━━━━━━━", text)
        # 저장된 원고는 그대로(내보낼 때만 바뀐다)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertIn("│ 레벨: 3", detail["content_md"])
        self.assertIn('data-sw-export="text_safe"', detail["content_md"])


class StatusWindowScreenTests(unittest.TestCase):
    def test_node_model(self) -> None:
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            raise unittest.SkipTest("node 없음")
        result = subprocess.run([node, str(NODE_TEST)], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_controls_scripts_and_locales(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for element_id in (
            "statusWindowMenu", "statusWindowMenuList", "statusWindowTidyButton", "statusWindowManageButton",
            "statsStatusWindowChip", "statusWindowPresetsModal", "statusWindowPresetList", "statusWindowTemplateSelect",
            "statusWindowPresetForm", "statusWindowPresetName", "statusWindowPresetTitle", "statusWindowFrameSeg",
            "statusWindowAlignSeg", "statusWindowLabelSeg", "statusWindowItems", "statusWindowAddItem",
            "statusWindowBgSeg", "statusWindowFontSeg", "statusWindowExportSeg", "statusWindowPreview",
            "statusWindowDuplicateButton", "statusWindowDeleteButton", "statusWindowSaveButton",
        ):
            self.assertIn(f'id="{element_id}"', html, element_id)
        self.assertIn('data-format="statusWindow"', html)
        self.assertIn('data-isl-fmt="statusWindow"', html)
        self.assertLess(html.index("/status_window_model.js"), html.index('src="/app.js'))
        # 원고 저장 때 표식이 지워지지 않아야 한다.
        keep = app_js[app_js.index("const EDITOR_DATA_ATTR_KEEP"):app_js.index("const SPAN_LAYOUT_PROPS")]
        for attr in ("data-status-window", "data-sw-frame", "data-sw-align", "data-sw-bg", "data-sw-font", "data-sw-export"):
            self.assertIn(f'"{attr}"', keep, attr)
        # 맞춤법·토리 체크는 상태창 글자를 뺀다.
        self.assertIn("getEditorPlainText(editor, { excludeStatusWindows: true }).trim()", app_js)
        self.assertIn("engine.analyze(tab, getEditorPlainText(null, { excludeStatusWindows: true })", app_js)

        locales = {
            lang: json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            for lang in ("ko", "en", "es")
        }
        used = set(re.findall(r"statusWindow\.([A-Za-z0-9_가-힣]+)", html + app_js)) - {"lastPreset"}
        start = app_js.index("function statusWindowTemplates")
        end = app_js.index("function normalizeStatusWindowPreset")
        used |= set(re.findall(r'(?:t|item)\("([^"]+)"', app_js[start:end]))
        start = app_js.index("function renderStatusWindowPresetForm")
        end = app_js.index("function renderStatusWindowPreview")
        used |= {k for k in re.findall(r'\bt\("([^"]+)"\)', app_js[start:end])}
        self.assertGreater(len(used), 50)
        for key in sorted(used):
            for lang, data in locales.items():
                self.assertIn(f"statusWindow.{key}", data, f"{lang}: {key}")
        for lang in ("en", "es"):
            for key in used:
                value = locales[lang][f"statusWindow.{key}"]
                self.assertIsNone(re.search(r"[가-힣]", value), f"{lang}: {key} = {value}")
                self.assertEqual(
                    re.findall(r"\$\{[^}]+\}", value),
                    re.findall(r"\$\{[^}]+\}", locales["ko"][f"statusWindow.{key}"]),
                    f"{lang}: {key}",
                )

    def test_pagewrite_treats_status_window_as_embed(self) -> None:
        js = (ROOT / "web" / "pagewrite_model.js").read_text(encoding="utf-8")
        self.assertIn('const STATUS_SELECTOR = "[data-status-window],.st-status-window";', js)
        self.assertIn('if (el.matches(STATUS_SELECTOR)) return "status";', js)


if __name__ == "__main__":
    unittest.main()
