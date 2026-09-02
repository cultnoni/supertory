"""Complete-status quote conversion and per-project completion guide flag."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class CompletionGuideTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _create_scene(self, title: str = "완성 안내") -> tuple[int, dict]:
        status, project = self.request("POST", "/api/projects", {"title": title, "main_genre": "판타지"})
        self.assertEqual(status, 201)
        project_id = int(project["id"])
        status, chapter = self.request(
            "POST", f"/api/projects/{project_id}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201)
        return project_id, scene

    def test_complete_converts_straight_quotes_and_is_idempotent(self) -> None:
        project_id, scene = self._create_scene()
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        status, _ = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "1화",
            "status": "complete",
            "content_md": '그는 "안녕" 하고 \'속삭였다\'.',
            "row_version": detail["row_version"],
        })
        self.assertEqual(status, 200)
        status, saved = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(saved["content_md"], "그는 “안녕” 하고 ‘속삭였다’.")
        self.assertEqual(saved["status"], "complete")

        status, _ = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "1화",
            "status": "draft",
            "content_md": saved["content_md"],
            "row_version": saved["row_version"],
        })
        self.assertEqual(status, 200)
        status, drafted = self.request("GET", f"/api/scenes/{scene['id']}")
        status, _ = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "1화",
            "status": "complete",
            "content_md": drafted["content_md"],
            "row_version": drafted["row_version"],
        })
        self.assertEqual(status, 200)
        status, again = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(again["content_md"], "그는 “안녕” 하고 ‘속삭였다’.")
        _ = project_id

    def test_existing_curly_quotes_are_not_rewritten(self) -> None:
        _project_id, scene = self._create_scene("이미 둥근")
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        curly = "그는 “안녕” 했다."
        status, _ = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "1화",
            "status": "complete",
            "content_md": curly,
            "row_version": detail["row_version"],
        })
        self.assertEqual(status, 200)
        status, saved = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(saved["content_md"], curly)

    def test_completion_guide_flag_is_per_project(self) -> None:
        project_a, _scene_a = self._create_scene("작품 A")
        project_b, _scene_b = self._create_scene("작품 B")

        status, outline_a = self.request("GET", f"/api/projects/{project_a}/outline")
        self.assertEqual(status, 200)
        self.assertFalse(bool(outline_a["project"].get("completion_guide_shown")))

        status, marked = self.request(
            "POST", f"/api/projects/{project_a}/completion-guide-shown", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(marked["completion_guide_shown"])

        status, outline_a = self.request("GET", f"/api/projects/{project_a}/outline")
        self.assertTrue(bool(outline_a["project"].get("completion_guide_shown")))

        status, outline_b = self.request("GET", f"/api/projects/{project_b}/outline")
        self.assertEqual(status, 200)
        self.assertFalse(bool(outline_b["project"].get("completion_guide_shown")))


class CompletionGuideCopyTests(unittest.TestCase):
    def test_complete_guide_is_a_single_card_with_viewer_line(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
        ko = json.loads((root / "web" / "locales" / "ko.json").read_text(encoding="utf-8"))

        self.assertIn('id="completionGuideCard"', html)
        self.assertIn('id="completionGuideDismissBtn"', html)
        self.assertIn("app.다시_보지_않기", html)
        self.assertIn("app.완성_안내_보호", html)
        self.assertIn("app.완성_안내_따옴표", html)
        self.assertIn('id="completionGuideTraitItem"', html)
        self.assertIn("data-i18n-html=\"app.완성_안내_가상독자\"", html)
        self.assertNotIn("app.완성_안내_뷰어_댓글", html)
        self.assertIn("완성되면 <strong>[뷰어]</strong>에서 가상독자 댓글을 확인할 수 있어요", html)

        self.assertIn("function maybeShowCompletionGuideOnComplete", app_js)
        self.assertIn("function updateCompletionGuideTraitLine", app_js)
        self.assertNotIn("완성_처리됐어요_가상독자_댓글도_받아볼_수", app_js)
        self.assertNotIn("app.가상독자_댓글은_뷰어에서_확인할_수_있어요", app_js)

        self.assertEqual(
            ko["app.완성_안내_가상독자"],
            "완성되면 <strong>[뷰어]</strong>에서 가상독자 댓글을 확인할 수 있어요",
        )
        self.assertEqual(ko["app.완성_안내_제목"], "✅ 완성 처리됐어요")
        self.assertIn("app.완성_안내_인물", ko)
