"""Tory notification store: dedupe, dismiss-forever, and snooze wake."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

import app
import tory_notifications


class ToryNotificationApiTests(unittest.TestCase):
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
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        if not raw:
            return response.status, {}
        return response.status, json.loads(raw.decode("utf-8"))

    def create_project(self) -> int:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "웹소설 알림",
                "purpose": "web_novel",
                "cluster_id": "webnovel",
                "main_genre": "fantasy",
                "sub_genre": "regression",
            },
        )
        self.assertEqual(status, 201, project)
        self.assertNotIn("literary_form", project)
        return int(project["id"])

    def test_dedupe_updates_open_row_and_forever_blocks_another(self) -> None:
        project_id = self.create_project()
        actions = [{"id": "no", "label": "아니요", "type": "dismiss_forever", "target": {}}]
        with app.database() as connection:
            first = tory_notifications.create_tory_notification(
                connection,
                project_id,
                kind="style_choice",
                title="처음",
                body="본문",
                payload={"quotes": ["하나"]},
                actions=actions,
                dedupe_key="choice:repeat",
            )
            second = tory_notifications.create_tory_notification(
                connection,
                project_id,
                kind="style_choice",
                title="갱신",
                body="새 본문",
                payload={"quotes": ["둘"]},
                actions=actions,
                dedupe_key="choice:repeat",
            )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["updated"])
        self.assertEqual(first["notification"]["id"], second["notification"]["id"])
        self.assertEqual(second["notification"]["title"], "갱신")

        status, done = self.request(
            "POST",
            f"/api/projects/{project_id}/tory-notifications/{first['notification']['id']}/actions/no",
            {},
        )
        self.assertEqual(status, 200, done)
        self.assertEqual(done["notification"]["status"], "dismissed")
        self.assertTrue(done["notification"]["dismissed_forever"])

        with app.database() as connection:
            blocked = tory_notifications.create_tory_notification(
                connection,
                project_id,
                kind="style_choice",
                title="다시",
                body="나오면 안 됨",
                actions=actions,
                dedupe_key="choice:repeat",
            )
            count = connection.execute(
                "SELECT COUNT(*) FROM tory_notification WHERE project_id = ? AND dedupe_key = ?",
                (project_id, "choice:repeat"),
            ).fetchone()[0]
        self.assertTrue(blocked["blocked"])
        self.assertFalse(blocked["created"])
        self.assertEqual(blocked["notification"]["title"], "갱신")
        self.assertEqual(count, 1)

        status, listed = self.request("GET", f"/api/projects/{project_id}/tory-notifications")
        self.assertEqual(status, 200, listed)
        self.assertEqual(listed, [])

    def test_snooze_hides_until_it_is_due(self) -> None:
        project_id = self.create_project()
        with app.database() as connection:
            created = tory_notifications.create_tory_notification(
                connection,
                project_id,
                title="나중에",
                dedupe_key="fixture:later",
                actions=[{"id": "later", "label": "나중에", "type": "snooze", "target": {"hours": 24}}],
            )
        notification_id = created["notification"]["id"]
        status, snoozed = self.request(
            "POST",
            f"/api/projects/{project_id}/tory-notifications/{notification_id}/actions/later",
            {},
        )
        self.assertEqual(status, 200, snoozed)
        self.assertEqual(snoozed["notification"]["status"], "snoozed")
        status, hidden = self.request("GET", f"/api/projects/{project_id}/tory-notifications")
        self.assertEqual(hidden, [])

        past = tory_notifications.utc_stamp(tory_notifications.utc_now() - timedelta(minutes=1))
        with app.database() as connection:
            connection.execute(
                "UPDATE tory_notification SET snooze_until = ? WHERE id = ?",
                (past, notification_id),
            )
        status, visible = self.request("GET", f"/api/projects/{project_id}/tory-notifications")
        self.assertEqual(status, 200, visible)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["status"], "unread")
        self.assertIsNone(visible[0]["snooze_until"])
