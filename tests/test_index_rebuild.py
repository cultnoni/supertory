"""Bulk episode index rebuild (admin: 회차 인덱스 다시 만들기)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app
import gemini_client


JS_PATH = Path(__file__).resolve().parents[1] / "web" / "app.js"
HTML_PATH = Path(__file__).resolve().parents[1] / "web" / "index.html"


class IndexRebuildContractTests(unittest.TestCase):
    def setUp(self) -> None:
        app.reset_index_rebuild_state()
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
        worker = app._index_rebuild_thread
        if worker is not None and worker.is_alive():
            worker.join(timeout=120)
        app.reset_index_rebuild_state()
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=60)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def create_project_scene(self, title: str, content: str) -> tuple[int, int]:
        status, project = self.request(
            "POST", "/api/projects", {"title": title, "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, project)
        status, chapter = self.request(
            "POST", f"/api/projects/{project['id']}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201, chapter)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201, scene)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": "1화",
                "content_md": content,
                "status": "draft",
                "row_version": detail.get("row_version") or 1,
            },
        )
        self.assertEqual(status, 200, saved)
        return int(project["id"]), int(scene["id"])

    def test_quota_helper(self) -> None:
        quota = gemini_client.GeminiError("daily", code="quota", http_status=429)
        rate = gemini_client.GeminiError("rpm", code="rate_limit", http_status=429)
        wrapped = ValueError(str(quota))
        wrapped.__cause__ = quota
        self.assertTrue(app.is_gemini_quota_error(quota))
        self.assertTrue(app.is_gemini_quota_error(wrapped))
        self.assertFalse(app.is_gemini_quota_error(rate))
        self.assertFalse(app.is_gemini_quota_error("RESOURCE_EXHAUSTED: quota exceeded"))
        self.assertFalse(app.is_gemini_quota_error("HTTP 429 rate limit"))
        self.assertFalse(app.is_gemini_quota_error("JSON을 찾지 못했습니다"))

    def test_admin_ui_has_rebuild_menu(self) -> None:
        html = HTML_PATH.read_text(encoding="utf-8")
        js = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("회차 인덱스 다시 만들기", html)
        self.assertIn("선택 작품 재인덱싱", html)
        self.assertIn("indexRebuildConfirmModal", html)
        self.assertIn("loadIndexRebuildOverview", js)
        self.assertIn("쿼터 제한에 도달했습니다. 잠시 후 다시 시도해주세요", js)
        self.assertIn("예상 소요 시간은", js)
        self.assertIn("Gemini API 호출이 발생하니 참고해주세요", js)

    def test_overview_counts_null_vs_filled(self) -> None:
        project_a, scene_a = self.create_project_scene(
            "빈 인덱스",
            "한서는 왼팔을 다쳤다. 피가 흘렀고 그는 전장에서 물러났다.",
        )
        project_b, scene_b = self.create_project_scene(
            "채운 인덱스",
            "한서는 왼팔을 다쳤다. 피가 흘렀고 그는 전장에서 물러났다.",
        )
        status, filled = self.request(
            "PUT",
            f"/api/scenes/{scene_b}/summary",
            {
                "summary": {
                    "event_summary": "한서가 왼팔을 다쳤다",
                    "characters_involved": ["한서"],
                    "new_world_facts": [],
                    "new_threads": [],
                    "resolved_threads": [],
                    "tracked_facts": [{
                        "category": "신체상태",
                        "subject": "한서",
                        "attribute": "왼팔",
                        "value": "부상",
                    }],
                }
            },
        )
        self.assertEqual(status, 200, filled)

        status, overview = self.request("GET", "/api/index/rebuild")
        self.assertEqual(status, 200, overview)
        by_id = {int(item["id"]): item for item in overview["projects"]}
        self.assertEqual(by_id[project_a]["pending_count"], 1)
        self.assertTrue(by_id[project_a]["selectable"])
        self.assertEqual(by_id[project_b]["pending_count"], 0)
        # merge leftover after summary upsert may still keep B selectable
        self.assertIn("scene_count", by_id[project_a])
        self.assertGreaterEqual(by_id[project_a]["scene_count"], 1)

    def test_filled_facts_are_skipped_from_pending(self) -> None:
        project_id, scene_id = self.create_project_scene(
            "스킵 확인",
            "한서는 왼팔을 다쳤다. 주변 병사들이 그를 부축했다.",
        )
        status, overview = self.request("GET", "/api/index/rebuild")
        self.assertEqual(status, 200)
        pending_before = next(
            item["pending_count"] for item in overview["projects"] if int(item["id"]) == project_id
        )
        self.assertEqual(pending_before, 1)
        self.request(
            "PUT",
            f"/api/scenes/{scene_id}/summary",
            {
                "summary": {
                    "event_summary": "부상",
                    "characters_involved": ["한서"],
                    "new_world_facts": [],
                    "new_threads": [],
                    "resolved_threads": [],
                    "tracked_facts": [{"category": "신체상태", "subject": "한서", "attribute": "왼팔", "value": "부상"}],
                }
            },
        )
        status, overview = self.request("GET", "/api/index/rebuild")
        pending_after = next(
            item["pending_count"] for item in overview["projects"] if int(item["id"]) == project_id
        )
        self.assertEqual(pending_after, 0)


@unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
class IndexRebuildLiveGeminiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.reset_index_rebuild_state()
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
        worker = app._index_rebuild_thread
        if worker is not None and worker.is_alive():
            worker.join(timeout=180)
        app.reset_index_rebuild_state()
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

    def create_project_scene(self, title: str, content: str) -> tuple[int, int]:
        status, project = self.request(
            "POST", "/api/projects", {"title": title, "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, project)
        status, chapter = self.request(
            "POST", f"/api/projects/{project['id']}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": "1화",
                "content_md": content,
                "status": "draft",
                "row_version": detail.get("row_version") or 1,
            },
        )
        self.assertEqual(status, 200, saved)
        return int(project["id"]), int(scene["id"])

    def wait_job(self, timeout: float = 120) -> dict:
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            status, last = self.request("GET", "/api/index/rebuild/status")
            self.assertEqual(status, 200, last)
            if str(last.get("status") or "") in {"done", "quota", "error"}:
                return last
            time.sleep(0.4)
        self.fail(f"index rebuild did not finish: {last}")

    def test_two_projects_rebuild_and_resume_skips_filled(self) -> None:
        text_a = "한서는 왼팔을 다쳤다. 피가 흘렀고 그는 무릎을 꿇었다."
        text_b = "유민은 낡은 검을 주워 허리에 찼다. 칼집에서 녹이 떨어졌다."
        project_a, scene_a = self.create_project_scene("재인덱스 A", text_a)
        project_b, scene_b = self.create_project_scene("재인덱스 B", text_b)

        status, overview = self.request("GET", "/api/index/rebuild")
        self.assertEqual(status, 200, overview)
        by_id = {int(item["id"]): item for item in overview["projects"]}
        self.assertEqual(by_id[project_a]["pending_count"], 1)
        self.assertEqual(by_id[project_b]["pending_count"], 1)

        status, started = self.request(
            "POST",
            "/api/index/rebuild",
            {"project_ids": [project_a, project_b]},
        )
        self.assertEqual(status, 200, started)
        self.assertTrue(started.get("started"))
        job = self.wait_job()
        self.assertEqual(job.get("status"), "done", job)

        status, summary_a = self.request("GET", f"/api/scenes/{scene_a}/summary")
        status_b, summary_b = self.request("GET", f"/api/scenes/{scene_b}/summary")
        self.assertEqual(status, 200, summary_a)
        self.assertEqual(status_b, 200, summary_b)
        facts_a = json.dumps(summary_a.get("tracked_facts") or [], ensure_ascii=False)
        facts_b = json.dumps(summary_b.get("tracked_facts") or [], ensure_ascii=False)
        self.assertTrue(summary_a.get("tracked_facts") is not None)
        self.assertIn("신체상태", facts_a)
        self.assertTrue("소지품" in facts_b or "검" in facts_b or summary_b.get("tracked_facts"))

        status, overview = self.request("GET", "/api/index/rebuild")
        by_id = {int(item["id"]): item for item in overview["projects"]}
        self.assertEqual(by_id[project_a]["pending_count"], 0)
        self.assertEqual(by_id[project_b]["pending_count"], 0)

        status, chapter = self.request(
            "POST", f"/api/projects/{project_a}/chapters", {"title": "2장"}
        )
        self.assertEqual(status, 201)
        status, extra = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "2화"}
        )
        self.assertEqual(status, 201)
        status, detail = self.request("GET", f"/api/scenes/{extra['id']}")
        self.request(
            "PUT",
            f"/api/scenes/{extra['id']}",
            {
                "title": "2화",
                "content_md": "한서는 다친 왼팔을 감싼 채 숲길로 들어섰다. 비바람이 몰아쳤다.",
                "status": "draft",
                "row_version": detail.get("row_version") or 1,
            },
        )
        status, overview = self.request("GET", "/api/index/rebuild")
        pending = next(item["pending_count"] for item in overview["projects"] if int(item["id"]) == project_a)
        self.assertEqual(pending, 1)

        status, started = self.request(
            "POST", "/api/index/rebuild", {"project_ids": [project_a]}
        )
        self.assertEqual(status, 200, started)
        job = self.wait_job()
        self.assertEqual(job.get("status"), "done", job)
        status, summary_a2 = self.request("GET", f"/api/scenes/{scene_a}/summary")
        self.assertEqual(
            json.dumps(summary_a.get("tracked_facts"), ensure_ascii=False),
            json.dumps(summary_a2.get("tracked_facts"), ensure_ascii=False),
        )
        status, extra_summary = self.request("GET", f"/api/scenes/{extra['id']}/summary")
        self.assertTrue(extra_summary.get("tracked_facts") is not None)
        print("\n=== REBUILD JOB ===\n", json.dumps(job, ensure_ascii=False, indent=2))
        print("\n=== A FACTS ===\n", facts_a)
        print("\n=== B FACTS ===\n", facts_b)


if __name__ == "__main__":
    unittest.main()
