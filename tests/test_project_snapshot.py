"""Project-wide SQLite snapshots (admin 버전 정보)."""

from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app
import project_snapshot


class ProjectSnapshotTests(unittest.TestCase):
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
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _create_work(self, title: str, content: str) -> tuple[int, int]:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": title, "purpose": "novel", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201)
        status, chapter = self.request(
            "POST",
            f"/api/projects/{project['id']}/chapters",
            {"title": "1장"},
        )
        self.assertEqual(status, 201)
        status, scene = self.request(
            "POST",
            f"/api/chapters/{chapter['id']}/scenes",
            {"title": "시작"},
        )
        self.assertEqual(status, 201)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        status, _ = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": "시작",
                "status": "draft",
                "synopsis_md": "",
                "notes_md": "",
                "content_md": content,
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200)
        return int(project["id"]), int(scene["id"])

    def test_snapshot_is_project_scoped_and_restore_preserves_other_works(self) -> None:
        project_a, scene_a = self._create_work("작품 A", "알파 원고")
        project_b, scene_b = self._create_work("작품 B", "베타 원고")

        created = project_snapshot.create_snapshot(
            app.DATABASE_PATH, app.DATA_DIR, project_a
        )
        snap_path = project_snapshot.snapshots_dir(app.DATA_DIR) / created["filename"]
        self.assertTrue(snap_path.is_file())
        with sqlite3.connect(snap_path) as snap:
            ids = [row[0] for row in snap.execute("SELECT id FROM project").fetchall()]
            self.assertEqual(ids, [project_a])
            texts = [
                row[0]
                for row in snap.execute("SELECT content_md FROM scene_revision").fetchall()
            ]
            joined = "\n".join(texts)
            self.assertIn("알파 원고", joined)
            self.assertNotIn("베타 원고", joined)

        status, detail_a = self.request("GET", f"/api/scenes/{scene_a}")
        self.assertEqual(status, 200)
        status, _ = self.request(
            "PUT",
            f"/api/scenes/{scene_a}",
            {
                "title": "시작",
                "status": "draft",
                "synopsis_md": "",
                "notes_md": "",
                "content_md": "알파 원고 이후 수정",
                "row_version": detail_a["row_version"],
            },
        )
        self.assertEqual(status, 200)

        restored = project_snapshot.restore_snapshot(
            app.DATABASE_PATH, app.DATA_DIR, project_a, created["filename"]
        )
        self.assertTrue(restored["safety"])
        status, after_a = self.request("GET", f"/api/scenes/{scene_a}")
        self.assertEqual(status, 200)
        self.assertEqual(after_a["content_md"], "알파 원고")
        status, after_b = self.request("GET", f"/api/scenes/{scene_b}")
        self.assertEqual(status, 200)
        self.assertEqual(after_b["content_md"], "베타 원고")

        names = [item["filename"] for item in project_snapshot.list_snapshots(app.DATA_DIR, project_a)]
        self.assertIn(restored["safety"]["filename"], names)

    def test_keeps_four_snapshots_and_due_interval(self) -> None:
        project_id, _scene_id = self._create_work("보관 정책", "본문")
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        names = []
        for index in range(5):
            created = project_snapshot.create_snapshot(
                app.DATABASE_PATH,
                app.DATA_DIR,
                project_id,
                now=base + timedelta(days=index),
            )
            names.append(created["filename"])
        listed = project_snapshot.list_snapshots(app.DATA_DIR, project_id)
        self.assertEqual(len(listed), 4)
        self.assertNotIn(names[0], [item["filename"] for item in listed])
        self.assertEqual(listed[-1]["filename"], names[1])

        latest = project_snapshot.latest_snapshot_time(app.DATA_DIR, project_id)
        self.assertIsNotNone(latest)
        skip = project_snapshot.maybe_create_due_snapshots(
            app.DATABASE_PATH,
            app.DATA_DIR,
            now=base + timedelta(days=8),
            interval=timedelta(days=7),
        )
        self.assertEqual(skip, [])
        due = project_snapshot.maybe_create_due_snapshots(
            app.DATABASE_PATH,
            app.DATA_DIR,
            now=base + timedelta(days=12),
            interval=timedelta(days=7),
        )
        self.assertEqual(len(due), 1)
        self.assertEqual(int(due[0]["project_id"]), project_id)

    def test_http_list_create_and_restore(self) -> None:
        project_id, scene_id = self._create_work("API 작품", "복구 전 원고")
        status, created = self.request("POST", f"/api/projects/{project_id}/snapshots", {})
        self.assertEqual(status, 201)
        filename = created["filename"]
        status, listed = self.request("GET", f"/api/projects/{project_id}/snapshots")
        self.assertEqual(status, 200)
        self.assertEqual(listed["keep_count"], 4)
        self.assertEqual(len(listed["snapshots"]), 1)
        self.assertEqual(listed["snapshots"][0]["filename"], filename)

        status, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200)
        status, _ = self.request(
            "PUT",
            f"/api/scenes/{scene_id}",
            {
                "title": "시작",
                "status": "draft",
                "synopsis_md": "",
                "notes_md": "",
                "content_md": "복구 후 사라져야 하는 문장",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200)

        status, result = self.request(
            "POST",
            f"/api/projects/{project_id}/snapshots/restore",
            {"filename": filename},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["reload"])
        self.assertTrue(result["safety"])
        status, restored = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200)
        self.assertEqual(restored["content_md"], "복구 전 원고")


class ProjectSnapshotUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parent.parent
        cls.html = (root / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (root / "web" / "app.js").read_text(encoding="utf-8")
        cls.locales = {
            lang: json.loads((root / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            for lang in ("ko", "en", "es")
        }

    def test_admin_settings_has_version_section(self) -> None:
        self.assertIn('id="versionSnapshotBox"', self.html)
        self.assertIn('data-i18n="index.버전_정보"', self.html)
        self.assertIn('id="versionSnapshotConfirmModal"', self.html)
        self.assertIn("loadVersionSnapshots", self.js)
        self.assertIn("restoreVersionSnapshotNow", self.js)
        for lang, payload in self.locales.items():
            self.assertIn("index.버전_정보", payload, lang)
            self.assertIn("index.스냅샷_복구_확인", payload, lang)
            self.assertIn("${date}", payload["index.스냅샷_복구_확인"], lang)
