"""첨삭 피드백 HTTP API (가짜 Claude, 임시 DB)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app
import feedback_api
import feedback_store
from feedback_pipeline.claude_client import (
    set_client_for_tests,
    set_configured_for_tests,
)
from feedback_pipeline.paragraphs import paragraphs_from_html
from test_feedback_pipeline import (
    MANUSCRIPT,
    PipelineFake,
    _default_consistency,
    _default_report,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "feedback_paragraphs.json"


class SlowFake(PipelineFake):
    def __init__(self, delay: float = 1.2, **kwargs) -> None:
        super().__init__(**kwargs)
        self.delay = delay

    def generate(self, prompt: str, **kwargs):
        if "첨삭 카드 1장" in prompt:
            time.sleep(self.delay)
        return super().generate(prompt, **kwargs)


class FeedbackApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        feedback_api.reset_runtime_for_tests()
        set_configured_for_tests(True)
        set_client_for_tests(
            PipelineFake(
                consistency=_default_consistency(),
                report=_default_report(),
            )
        )
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        deadline = time.time() + 20
        while time.time() < deadline and feedback_api._active_count():
            time.sleep(0.05)
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        set_client_for_tests(None)
        set_configured_for_tests(None)
        feedback_api.reset_runtime_for_tests()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=30
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(
            method, path, body, {"Content-Type": "application/json"} if body else {}
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        result = json.loads(raw) if raw else {}
        return response.status, result

    def create_project_scene(self, content: str, title: str = "1화") -> tuple[int, int]:
        status, project = self.request(
            "POST", "/api/projects", {"title": "첨삭 API", "main_genre": "웹소설"}
        )
        self.assertEqual(status, 201, project)
        project_id = int(project["id"])
        status, chapter = self.request(
            "POST", f"/api/projects/{project_id}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201, chapter)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": title}
        )
        self.assertEqual(status, 201, scene)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": title,
                "content_md": content,
                "status": "draft",
                "row_version": detail.get("row_version") or 1,
            },
        )
        self.assertEqual(status, 200, saved)
        return project_id, int(scene["id"])

    def poll_run(self, run_id: int, *wanted: str, timeout: float = 12.0) -> dict:
        deadline = time.time() + timeout
        last: dict | None = None
        while time.time() < deadline:
            status, body = self.request("GET", f"/api/feedback/runs/{run_id}")
            self.assertEqual(status, 200, body)
            last = body if isinstance(body, dict) else None
            if last and last.get("status") in wanted:
                return last
            time.sleep(0.05)
        self.fail(f"실행 {run_id}가 {wanted}가 되지 않았습니다: {last}")

    def test_status_does_not_expose_key(self) -> None:
        status, body = self.request("GET", "/api/feedback/status")
        self.assertEqual(status, 200)
        self.assertIn("configured", body)
        self.assertIn("model", body)
        blob = json.dumps(body)
        self.assertNotIn("ANTHROPIC", blob)
        self.assertNotIn("api_key", blob.lower())
        self.assertNotIn("sk-", blob)

    def test_status_default_lens_and_post_body(self) -> None:
        web_id, scene_id = self.create_project_scene(MANUSCRIPT)
        status, body = self.request("GET", f"/api/feedback/status?project_id={web_id}")
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("default_explanation_lens"), "normal")
        status, created = self.request(
            "POST",
            f"/api/projects/{web_id}/feedback/runs",
            {"scene_id": scene_id, "explanation_lens": "strong"},
        )
        self.assertIn(status, (200, 201), created)
        run = self.poll_run(int(created["run_id"]), "ok", "partial", "failed")
        self.assertEqual((run.get("params") or {}).get("explanation_lens"), "strong")

        status, literary = self.request(
            "POST",
            "/api/projects",
            {"title": "문학 시험", "main_genre": "문학", "sub_genre": "에세이"},
        )
        self.assertEqual(status, 201, literary)
        status, lens_body = self.request(
            "GET", f"/api/feedback/status?project_id={literary['id']}"
        )
        self.assertEqual(status, 200, lens_body)
        self.assertEqual(lens_body.get("default_explanation_lens"), "strong")

    def test_create_polls_to_ok_with_cards_and_progress(self) -> None:
        project_id, scene_id = self.create_project_scene(MANUSCRIPT)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id, "explanation_lens": "normal"},
        )
        self.assertIn(status, (200, 201), created)
        self.assertEqual(created["status"], "running")
        self.assertGreater(created["paragraph_count"], 0)
        self.assertTrue(created.get("source_hash"))
        run = self.poll_run(int(created["run_id"]), "ok", "partial")
        self.assertEqual(run["status"], "ok")
        self.assertTrue(run.get("cards"))
        self.assertTrue(run.get("report") or run.get("report_md"))
        self.assertTrue(run.get("paragraphs"))
        self.assertEqual(run["paragraphs"][0].keys() >= {"i", "type", "text"}, True)
        progress = run.get("progress") or {}
        self.assertEqual(progress.get("stage"), "done")
        self.assertIn("cost_usd", run.get("usage") or {})

    def test_duplicate_running_is_409_missing_key_400_missing_scene_404(self) -> None:
        project_id, scene_id = self.create_project_scene(MANUSCRIPT)
        set_client_for_tests(
            SlowFake(
                delay=1.5,
                consistency=_default_consistency(),
                report=_default_report(),
            )
        )
        status, first = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertIn(status, (200, 201), first)
        status, dup = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertEqual(status, 409, dup)
        self.poll_run(int(first["run_id"]), "ok", "partial", "failed")

        set_configured_for_tests(False)
        status, missing_key = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertEqual(status, 400, missing_key)
        self.assertIn("ANTHROPIC_API_KEY", str(missing_key.get("error") or ""))
        self.assertNotIn("sk-", json.dumps(missing_key))
        set_configured_for_tests(True)

        status, missing_scene = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": 999999},
        )
        self.assertEqual(status, 404, missing_scene)

    def test_card_status_primary_and_delete(self) -> None:
        project_id, scene_id = self.create_project_scene(MANUSCRIPT)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertIn(status, (200, 201), created)
        run = self.poll_run(int(created["run_id"]), "ok", "partial")
        card_id = int(run["cards"][0]["id"])
        status, bad = self.request(
            "PUT", f"/api/feedback/cards/{card_id}", {"status": "nope"}
        )
        self.assertEqual(status, 400, bad)
        status, edited = self.request(
            "PUT",
            f"/api/feedback/cards/{card_id}",
            {"status": "applied_edited", "final_text": "고친 문장입니다."},
        )
        self.assertEqual(status, 200, edited)
        self.assertEqual(edited["status"], "applied_edited")
        self.assertEqual(edited["final_text"], "고친 문장입니다.")
        status, primary = self.request(
            "POST",
            f"/api/feedback/runs/{created['run_id']}/primary",
            {"scene_id": scene_id},
        )
        self.assertEqual(status, 200, primary)
        status, deleted = self.request(
            "DELETE", f"/api/feedback/runs/{created['run_id']}"
        )
        self.assertEqual(status, 200, deleted)
        status, gone = self.request("GET", f"/api/feedback/runs/{created['run_id']}")
        self.assertEqual(status, 404, gone)

    def test_cancel_keeps_saved_cards(self) -> None:
        project_id, scene_id = self.create_project_scene(MANUSCRIPT)
        set_client_for_tests(
            SlowFake(
                delay=3.0,
                consistency=_default_consistency(),
                report=_default_report(),
            )
        )
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertIn(status, (200, 201), created)
        run_id = int(created["run_id"])
        deadline = time.time() + 4
        saw_saved = False
        while time.time() < deadline:
            _st, body = self.request("GET", f"/api/feedback/runs/{run_id}")
            if isinstance(body, dict) and (
                body.get("cards")
                or str((body.get("progress") or {}).get("stage") or "")
                in {"dup", "consistency", "report", "cards"}
            ):
                saw_saved = True
                break
            time.sleep(0.03)
        self.assertTrue(saw_saved, "취소 전에 진행 상태를 보지 못했습니다.")
        status, cancelled = self.request("POST", f"/api/feedback/runs/{run_id}/cancel", {})
        self.assertEqual(status, 200, cancelled)
        run = self.poll_run(run_id, "partial", "failed")
        self.assertEqual(run["status"], "partial")
        self.assertTrue(run.get("cards"))

        status, created2 = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertIn(status, (200, 201), created2)
        status, busy_delete = self.request(
            "DELETE", f"/api/feedback/runs/{created2['run_id']}"
        )
        self.assertEqual(status, 409, busy_delete)
        self.request("POST", f"/api/feedback/runs/{created2['run_id']}/cancel", {})
        self.poll_run(int(created2["run_id"]), "partial", "ok", "failed")

    def test_startup_interrupt_and_import_legacy(self) -> None:
        project_id, scene_id = self.create_project_scene("한 문단입니다.")
        conn = app.connect()
        try:
            running = feedback_store.create_run(
                conn, project_id, "analyze", "claude-sonnet-5", "v", {}
            )
            feedback_store.add_run_scene(
                conn, running, project_id, scene_id, 0, "1화", 1, "h", []
            )
            with_report = feedback_store.create_run(
                conn, project_id, "analyze", "claude-sonnet-5", "v", {}
            )
            feedback_store.add_run_scene(
                conn, with_report, project_id, scene_id, 0, "1화", 1, "h2", []
            )
            feedback_store.update_run(
                conn, with_report, report_json={"summary": "남김"}, report_md="남김"
            )
            conn.commit()
            changed = feedback_api.interrupt_stale_runs(conn)
            conn.commit()
            self.assertGreaterEqual(changed, 2)
            a = feedback_store.get_run(conn, running)
            b = feedback_store.get_run(conn, with_report)
            self.assertEqual(a["status"], "failed")
            self.assertEqual(b["status"], "partial")
            self.assertEqual((a["params"] or {}).get("progress", {}).get("stage"), "interrupted")
        finally:
            conn.close()

        entries = [
            {"id": "legacy-1", "mode": "analyze", "text": "옛 리포트", "sceneId": scene_id},
            {"id": "legacy-1", "mode": "analyze", "text": "중복"},
            {"id": "legacy-2", "mode": "analyze", "text": "다른 것"},
        ]
        status, imported = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/import-legacy",
            {"entries": entries},
        )
        self.assertEqual(status, 200, imported)
        self.assertEqual(imported["imported"], 2)
        self.assertEqual(imported["skipped"], 1)
        status, again = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/import-legacy",
            {"entries": entries},
        )
        self.assertEqual(status, 200, again)
        self.assertEqual(again["imported"], 0)
        self.assertEqual(again["skipped"], 3)

    def test_html_paragraphs_match_fixture_rules(self) -> None:
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        case = next(item for item in cases if item["id"] == "html-scene-break-and-note")
        html = (
            "<p>본문 <strong>굵게</strong> 앞</p>"
            '<div class="manuscript-scene-break">* * *</div>'
            '<p data-author-note="1">작가 메모</p>'
            "<p>본문 뒤</p>"
        )
        expected = paragraphs_from_html(html)
        self.assertEqual(expected[0]["text"], "본문 굵게 앞")
        self.assertEqual(expected[1]["type"], "divider")
        self.assertEqual(len(expected), 3)
        fixture_expected = paragraphs_from_html(case["input"])
        self.assertEqual(fixture_expected, case["expected"])

        project_id, scene_id = self.create_project_scene(html)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertIn(status, (200, 201), created)
        run = self.poll_run(int(created["run_id"]), "ok", "partial", "failed")
        self.assertEqual(run["paragraphs"], expected)
        self.assertEqual(created["source_hash"], run["scenes"][0]["source_hash"])

    def test_list_runs_without_scene_id_includes_scene_fields(self) -> None:
        project_id, scene_id = self.create_project_scene("짧은 원고입니다. 두 문장입니다.")
        conn = app.connect()
        try:
            run_id = feedback_store.create_run(
                conn, project_id, "analyze", "test", "v1", {"fake": True}
            )
            feedback_store.add_run_scene(
                conn,
                run_id,
                project_id,
                scene_id,
                0,
                "1화",
                1,
                "hash-x",
                ["짧은 원고입니다."],
            )
            conn.commit()
        finally:
            conn.close()
        status, rows = self.request("GET", f"/api/projects/{project_id}/feedback/runs")
        self.assertEqual(status, 200, rows)
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["id"]), run_id)
        self.assertEqual(int(rows[0]["scene_id"]), scene_id)
        self.assertEqual(rows[0]["scene_title"], "1화")
        status, filtered = self.request(
            "GET", f"/api/projects/{project_id}/feedback/runs?scene_id={scene_id}"
        )
        self.assertEqual(status, 200, filtered)
        self.assertEqual(len(filtered), 1)
        status, other = self.request(
            "GET", f"/api/projects/{project_id}/feedback/runs?scene_id=999999"
        )
        self.assertEqual(status, 200, other)
        self.assertEqual(other, [])

    def test_card_comments_get_post_limit_and_missing(self) -> None:
        from feedback_pipeline.claude_client import FakeClaude

        project_id, scene_id = self.create_project_scene("짧은 원고입니다. 두 문장입니다.")
        conn = app.connect()
        try:
            run_id = feedback_store.create_run(
                conn, project_id, "analyze", "test", "v1", {}
            )
            feedback_store.add_run_scene(
                conn,
                run_id,
                project_id,
                scene_id,
                0,
                "1화",
                1,
                "hash-c",
                [{"i": 1, "text": "짧은 원고입니다.", "type": "text"}],
            )
            card_id = feedback_store.add_cards(
                conn,
                run_id,
                [
                    {
                        "scene_id": scene_id,
                        "kind": "structure",
                        "original_text": "짧은 원고입니다.",
                        "reason": "구조 지적",
                        "start_para": 1,
                        "end_para": 1,
                    }
                ],
            )[0]
            conn.commit()
        finally:
            conn.close()
        status, missing = self.request("GET", "/api/feedback/cards/999999/comments")
        self.assertEqual(status, 404, missing)
        status, missing_post = self.request(
            "POST", "/api/feedback/cards/999999/comments", {"message": "왜요?"}
        )
        self.assertEqual(status, 404, missing_post)
        status, empty = self.request("GET", f"/api/feedback/cards/{card_id}/comments")
        self.assertEqual(status, 200, empty)
        self.assertEqual(empty.get("comments"), [])
        fake = FakeClaude(responses=["짧게 답합니다. 무시하셔도 됩니다."])
        set_client_for_tests(fake)
        status, body = self.request(
            "POST",
            f"/api/feedback/cards/{card_id}/comments",
            {"message": "대화가 길어져서 설명을 넣은 건데요?"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body.get("comments") or []), 2)
        self.assertEqual(body["comments"][0]["role"], "user")
        self.assertEqual(body["comments"][1]["role"], "assistant")
        self.assertTrue(fake.prompts)
        status, run = self.request("GET", f"/api/feedback/runs/{run_id}")
        self.assertEqual(status, 200, run)
        self.assertEqual(int(run["cards"][0]["comment_count"]), 2)
        conn = app.connect()
        try:
            while len(feedback_store.list_card_comments(conn, card_id)) < 20:
                n = len(feedback_store.list_card_comments(conn, card_id))
                role = "user" if n % 2 == 0 else "assistant"
                feedback_store.add_card_comment(conn, card_id, role, f"더미{n}")
            conn.commit()
        finally:
            conn.close()
        blocked = FakeClaude(responses=["나오면 안 됨"])
        set_client_for_tests(blocked)
        status, limited = self.request(
            "POST",
            f"/api/feedback/cards/{card_id}/comments",
            {"message": "한 번 더"},
        )
        self.assertEqual(status, 409, limited)
        self.assertIn("너무 많이", str(limited.get("error") or ""))
        self.assertEqual(blocked.prompts, [])


class FeedbackFakeModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        self.original_fake = os.environ.get("SUPERTORY_FEEDBACK_FAKE")
        self.original_delay = os.environ.get("SUPERTORY_FEEDBACK_FAKE_DELAY")
        os.environ["SUPERTORY_FEEDBACK_FAKE"] = "1"
        os.environ["SUPERTORY_FEEDBACK_FAKE_DELAY"] = "0"
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        feedback_api.reset_runtime_for_tests()
        set_configured_for_tests(None)
        set_client_for_tests(None)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        deadline = time.time() + 20
        while time.time() < deadline and feedback_api._active_count():
            time.sleep(0.05)
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        set_client_for_tests(None)
        set_configured_for_tests(None)
        feedback_api.reset_runtime_for_tests()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        if self.original_fake is None:
            os.environ.pop("SUPERTORY_FEEDBACK_FAKE", None)
        else:
            os.environ["SUPERTORY_FEEDBACK_FAKE"] = self.original_fake
        if self.original_delay is None:
            os.environ.pop("SUPERTORY_FEEDBACK_FAKE_DELAY", None)
        else:
            os.environ["SUPERTORY_FEEDBACK_FAKE_DELAY"] = self.original_delay
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=30
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(
            method, path, body, {"Content-Type": "application/json"} if body else {}
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        result = json.loads(raw) if raw else {}
        return response.status, result

    def create_project_scene(self, content: str, title: str = "1화") -> tuple[int, int]:
        status, project = self.request(
            "POST", "/api/projects", {"title": "첨삭 시험", "main_genre": "웹소설"}
        )
        self.assertEqual(status, 201, project)
        project_id = int(project["id"])
        status, chapter = self.request(
            "POST", f"/api/projects/{project_id}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201, chapter)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": title}
        )
        self.assertEqual(status, 201, scene)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": title,
                "content_md": content,
                "status": "draft",
                "row_version": detail.get("row_version") or 1,
            },
        )
        self.assertEqual(status, 200, saved)
        return project_id, int(scene["id"])

    def test_fake_status_and_eight_plus_cards(self) -> None:
        status, body = self.request("GET", "/api/feedback/status")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("configured"))
        self.assertTrue(body.get("fake"))
        self.assertNotIn("ANTHROPIC", json.dumps(body))
        html = "".join(f"<p>{i}번째 문단입니다. 정원이 창가에 섰다.</p>" for i in range(1, 11))
        project_id, scene_id = self.create_project_scene(html)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertIn(status, (200, 201), created)
        deadline = time.time() + 20
        last = None
        while time.time() < deadline:
            st, run = self.request("GET", f"/api/feedback/runs/{created['run_id']}")
            self.assertEqual(st, 200, run)
            last = run
            if run.get("status") in {"ok", "partial"}:
                break
            time.sleep(0.05)
        self.assertIsNotNone(last)
        self.assertEqual(last["status"], "ok", last.get("params"))
        self.assertGreaterEqual(len(last.get("cards") or []), 8)
        self.assertTrue((last.get("params") or {}).get("fake"))

    def _wait_run(self, run_id: int) -> dict:
        deadline = time.time() + 20
        last = None
        while time.time() < deadline:
            status, run = self.request("GET", f"/api/feedback/runs/{run_id}")
            self.assertEqual(status, 200, run)
            last = run
            if run.get("status") in {"ok", "partial", "failed"}:
                break
            time.sleep(0.05)
        self.assertIsNotNone(last)
        return last

    def test_fake_br_html_spreads_diverse_cards(self) -> None:
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        html = next(item["input"] for item in cases if item["id"] == "html-br-only-webnovel")
        expected = paragraphs_from_html(html)
        self.assertGreaterEqual(len(expected), 8)
        project_id, scene_id = self.create_project_scene(html)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertIn(status, (200, 201), created)
        self.assertGreaterEqual(created.get("paragraph_count") or 0, 8)
        self.assertFalse(created.get("warnings"))
        run = self._wait_run(int(created["run_id"]))
        self.assertEqual(run["status"], "ok", run.get("params"))
        self.assertGreaterEqual(run.get("paragraph_count") or 0, 8)
        cards = run.get("cards") or []
        self.assertGreaterEqual(len(cards), 8)
        print("\n시험 모드 카드 목록")
        print("번호\t문단\t유형\t이유 앞 30자\t수정안")
        for index, card in enumerate(cards, start=1):
            start = card.get("start_para")
            end = card.get("end_para")
            loc = f"{start}" if start == end else f"{start}~{end}"
            kind = card.get("style_type") or card.get("kind") or ""
            reason = str(card.get("reason") or "").replace("\n", " ")
            has_sug = "있음" if card.get("suggestion") not in (None, "") else "없음"
            print(f"{index}\t{loc}\t{kind}\t{reason[:30]}\t{has_sug}")
        reasons = [str(c.get("reason") or "") for c in cards]
        self.assertEqual(len(reasons), len(set(reasons)))
        suggestions = [str(c.get("suggestion")) for c in cards if c.get("suggestion") not in (None, "")]
        self.assertEqual(len(suggestions), len(set(suggestions)))
        self.assertGreater(len({c.get("start_para") for c in cards}), 1)
        titles = [str(c.get("title") or "").strip() for c in cards]
        self.assertTrue(all(titles), titles)
        self.assertGreater(len(set(titles)), 3)
        self.assertGreaterEqual(
            sum(1 for c in cards if int(c.get("start_para") or 0) < int(c.get("end_para") or 0)),
            2,
        )

    def test_single_paragraph_warning_on_long_block(self) -> None:
        html = "<p>" + ("한 문단으로만 이어지는 원고입니다. " * 50) + "</p>"
        self.assertGreater(len(html), 800)
        project_id, scene_id = self.create_project_scene(html)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {"scene_id": scene_id},
        )
        self.assertIn(status, (200, 201), created)
        self.assertEqual(created.get("paragraph_count"), 1)
        codes = [w.get("code") for w in (created.get("warnings") or [])]
        self.assertIn("single_paragraph", codes)
        run = self._wait_run(int(created["run_id"]))
        self.assertEqual(run.get("paragraph_count"), 1)
        codes = [w.get("code") for w in (run.get("warnings") or [])]
        self.assertIn("single_paragraph", codes)


if __name__ == "__main__":
    unittest.main()
