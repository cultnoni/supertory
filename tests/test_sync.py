"""Offline-safe SuperTory desktop sync helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sync.device as device
import sync.pairing as pairing
import sync.supabase_client as supabase_client


class FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, table):
        self.table = table
        self._eq = {}
        self._op = "select"

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def eq(self, key, value):
        self._eq[key] = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self._op = "update"
        self.table.updates.append(payload)
        return self

    def insert(self, payload):
        self._op = "insert"
        self.table.rows.append(payload)
        return self

    def execute(self):
        if self._op == "select":
            wanted = self._eq.get("device_id") or self._eq.get("code")
            key = "device_id" if "device_id" in self._eq else "code"
            matches = [row for row in self.table.rows if row.get(key) == wanted]
            return FakeResult(matches)
        return FakeResult(self.table.rows[-1:] if self.table.rows else [])


class FakeTable:
    def __init__(self):
        self.rows = []
        self.updates = []

    def table(self, _name):
        return FakeQuery(self)


class SyncDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        supabase_client.reset_supabase_client_cache()

    def tearDown(self) -> None:
        supabase_client.reset_supabase_client_cache()
        self.tmp.cleanup()

    def test_device_id_persists_across_calls(self) -> None:
        with patch.object(device, "resolve_data_dir", return_value=self.data_dir):
            first = device.get_or_create_device_id()
            second = device.get_or_create_device_id()
        self.assertEqual(first, second)
        saved = json.loads((self.data_dir / "device_id.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["device_id"], first)

    def test_ensure_device_registered_noop_without_client(self) -> None:
        with patch.object(device, "get_supabase_client", return_value=None):
            device.ensure_device_registered()  # must not raise

    def test_ensure_device_registered_inserts_then_updates(self) -> None:
        store = FakeTable()
        with (
            patch.object(device, "get_supabase_client", return_value=store),
            patch.object(device, "resolve_data_dir", return_value=self.data_dir),
        ):
            device.ensure_device_registered()
            self.assertEqual(len(store.rows), 1)
            self.assertEqual(store.rows[0]["device_type"], "desktop")
            device.ensure_device_registered()
            self.assertEqual(len(store.rows), 1)
            self.assertEqual(len(store.updates), 1)
            self.assertIn("last_seen_at", store.updates[0])


class PairingCodeTests(unittest.TestCase):
    def tearDown(self) -> None:
        supabase_client.reset_supabase_client_cache()

    def test_generate_pairing_code_without_client(self) -> None:
        with patch.object(pairing, "get_supabase_client", return_value=None):
            self.assertIsNone(pairing.generate_pairing_code("dev-1"))

    def test_generate_pairing_code_retries_on_collision(self) -> None:
        store = FakeTable()
        store.rows.append(
            {"code": "111111", "desktop_device_id": "other", "used": False}
        )
        codes = iter(["111111", "222222"])
        with (
            patch.object(pairing, "get_supabase_client", return_value=store),
            patch.object(pairing, "_random_code", side_effect=lambda: next(codes)),
        ):
            result = pairing.generate_pairing_code("desktop-1")
        self.assertEqual(result["code"], "222222")
        self.assertIn("expires_at", result)
        self.assertEqual(store.rows[-1]["desktop_device_id"], "desktop-1")
        self.assertIs(store.rows[-1]["used"], False)


class PairingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        import threading

        import app

        self.app = app
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        supabase_client.reset_supabase_client_cache()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.DATA_DIR = self.original_data_dir
        self.app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()
        supabase_client.reset_supabase_client_cache()

    def test_pairing_code_returns_503_when_sync_off(self) -> None:
        import http.client
        import json as json_mod

        with patch.object(self.app, "get_supabase_client", return_value=None):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            connection.request("GET", "/api/pairing/code")
            response = connection.getresponse()
            body = json_mod.loads(response.read().decode("utf-8"))
            connection.close()
        self.assertEqual(response.status, 503)
        self.assertEqual(body, {"error": "sync_not_configured"})


class SnapshotFakeResult:
    def __init__(self, data=None):
        self.data = data or []


class SnapshotFakeQuery:
    def __init__(self, store):
        self.store = store
        self._eq = {}
        self._op = "select"
        self._payload = None
        self._on_conflict = None

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def eq(self, key, value):
        self._eq[key] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self._op = "update"
        self.store.updates.append(payload)
        return self

    def insert(self, payload):
        self._op = "insert"
        self.store.rows.append(payload)
        return self

    def upsert(self, payload, on_conflict=None, **_kwargs):
        self._op = "upsert"
        self._on_conflict = on_conflict
        self._payload = payload
        self.store.upserts.append({"payload": payload, "on_conflict": on_conflict})
        rows = payload if isinstance(payload, list) else [payload]
        self.store.rows.extend(rows)
        return self

    def execute(self):
        if self._op == "select":
            matches = list(self.store.rows)
            for key, value in self._eq.items():
                matches = [row for row in matches if row.get(key) == value]
            return SnapshotFakeResult(matches)
        return SnapshotFakeResult(self.store.rows[-1:] if self.store.rows else [])


class SnapshotFakeStore:
    def __init__(self):
        self.rows = []
        self.updates = []
        self.upserts = []


class SnapshotFakeClient:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = SnapshotFakeStore()
        return SnapshotFakeQuery(self.tables[name])


class SceneSnapshotSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        import app
        import sync.project_sync as project_sync

        self.app = app
        self.project_sync = project_sync
        self.tmp = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.tmp.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        supabase_client.reset_supabase_client_cache()

    def tearDown(self) -> None:
        supabase_client.reset_supabase_client_cache()
        self.app.DATA_DIR = self.original_data_dir
        self.app.DATABASE_PATH = self.original_database_path
        self.tmp.cleanup()

    def _seed_volume_and_chapters(self) -> tuple[int, list[int]]:
        with self.app.database() as connection:
            project_id = int(
                connection.execute(
                    "INSERT INTO project(title) VALUES ('스냅샷작품')"
                ).lastrowid
            )
            part_id = int(
                connection.execute(
                    "INSERT INTO part(project_id, title, sort_order) "
                    "VALUES (?, '1권', 0)",
                    (project_id,),
                ).lastrowid
            )
            volume_folder = int(
                connection.execute(
                    "INSERT INTO folder(project_id, parent_id, title, is_box, "
                    "sort_order, source_kind, source_id) "
                    "VALUES (?, NULL, '1권', 1, 0, 'part', ?)",
                    (project_id, part_id),
                ).lastrowid
            )
            scene_ids = []
            for index, chapter_title in enumerate(("3장", "4장")):
                chapter_id = int(
                    connection.execute(
                        "INSERT INTO chapter(project_id, part_id, title, sort_order) "
                        "VALUES (?, ?, ?, ?)",
                        (project_id, part_id, chapter_title, index),
                    ).lastrowid
                )
                folder_id = int(
                    connection.execute(
                        "INSERT INTO folder(project_id, parent_id, title, is_box, "
                        "sort_order, source_kind, source_id) "
                        "VALUES (?, ?, ?, 0, ?, 'chapter', ?)",
                        (project_id, volume_folder, chapter_title, index, chapter_id),
                    ).lastrowid
                )
                scene_id = int(
                    connection.execute(
                        "INSERT INTO scene(project_id, chapter_id, folder_id, title, sort_order) "
                        "VALUES (?, ?, ?, ?, 0)",
                        (project_id, chapter_id, folder_id, f"{chapter_title} 회차"),
                    ).lastrowid
                )
                connection.execute(
                    "INSERT INTO scene_revision(scene_id, revision_no, content_md, is_current) "
                    "VALUES (?, 1, ?, ?)",
                    (scene_id, f"{chapter_title} 초고", 0 if index == 0 else 1),
                )
                if index == 0:
                    connection.execute(
                        "INSERT INTO scene_revision(scene_id, revision_no, content_md, is_current) "
                        "VALUES (?, 2, ?, 1)",
                        (scene_id, f"{chapter_title} 원고"),
                    )
                scene_ids.append(scene_id)
            extra_chapter = int(
                connection.execute(
                    "INSERT INTO chapter(project_id, part_id, title, sort_order) "
                    "VALUES (?, ?, '5장', 2)",
                    (project_id, part_id),
                ).lastrowid
            )
            extra_folder = int(
                connection.execute(
                    "INSERT INTO folder(project_id, parent_id, title, is_box, "
                    "sort_order, source_kind, source_id) "
                    "VALUES (?, ?, '5장', 0, 2, 'chapter', ?)",
                    (project_id, volume_folder, extra_chapter),
                ).lastrowid
            )
            for extra_index in range(3):
                scene_id = int(
                    connection.execute(
                        "INSERT INTO scene(project_id, chapter_id, folder_id, title, sort_order) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            project_id,
                            extra_chapter,
                            extra_folder,
                            f"5장 회차{extra_index + 1}",
                            extra_index,
                        ),
                    ).lastrowid
                )
                connection.execute(
                    "INSERT INTO scene_revision(scene_id, revision_no, content_md, is_current) "
                    "VALUES (?, 1, ?, 1)",
                    (scene_id, f"5장 본문{extra_index + 1}"),
                )
                scene_ids.append(scene_id)
        return project_id, scene_ids

    def test_sync_scenes_snapshot_noop_without_client(self) -> None:
        with patch.object(self.project_sync, "get_supabase_client", return_value=None):
            self.project_sync.sync_scenes_snapshot(1)

    def test_sync_scenes_snapshot_upserts_folder_path_and_revision(self) -> None:
        project_id, scene_ids = self._seed_volume_and_chapters()
        client = SnapshotFakeClient()
        with (
            patch.object(self.project_sync, "get_supabase_client", return_value=client),
            patch.object(self.project_sync, "_SCENE_SNAPSHOT_BATCH_SIZE", 2),
        ):
            self.project_sync.sync_scenes_snapshot(project_id)

        projects = client.tables["projects"].rows
        self.assertEqual(len(projects), 1)
        remote_id = projects[0]["id"]
        scene_store = client.tables["scenes"]
        upserted = []
        for batch in scene_store.upserts:
            self.assertEqual(batch["on_conflict"], "project_id,local_scene_id")
            upserted.extend(batch["payload"])
        self.assertGreaterEqual(len(scene_store.upserts), 3)
        self.assertEqual([row["local_scene_id"] for row in upserted], scene_ids)
        self.assertEqual(upserted[0]["folder_path"], "1권/3장")
        self.assertEqual(upserted[1]["folder_path"], "1권/4장")
        self.assertEqual(upserted[0]["content_snapshot"], "3장 원고")
        self.assertEqual(upserted[0]["snapshot_revision_no"], 2)
        self.assertEqual(upserted[0]["project_id"], remote_id)
        self.assertEqual(upserted[2]["folder_path"], "1권/5장")


class MobileDraftSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        import sync.project_sync as project_sync

        self.project_sync = project_sync

    def test_fetch_pending_drafts_empty_without_client(self) -> None:
        with patch.object(self.project_sync, "get_supabase_client", return_value=None):
            self.assertEqual(self.project_sync.fetch_pending_drafts(1), [])

    def test_fetch_pending_drafts_returns_pending_rows(self) -> None:
        client = SnapshotFakeClient()
        client.tables["projects"] = SnapshotFakeStore()
        client.tables["projects"].rows.append({"id": "remote-uuid", "local_project_id": 9})
        drafts = SnapshotFakeStore()
        drafts.rows.extend(
            [
                {
                    "id": "d1",
                    "project_id": "remote-uuid",
                    "local_scene_id": 3,
                    "content": "폰에서 씀",
                    "based_on_revision_no": 2,
                    "created_at": "2026-08-21T00:00:00Z",
                    "status": "pending",
                },
                {
                    "id": "d2",
                    "project_id": "remote-uuid",
                    "local_scene_id": 4,
                    "content": "이미 반영",
                    "based_on_revision_no": 1,
                    "status": "merged",
                },
            ]
        )
        client.tables["scene_drafts"] = drafts
        with (
            patch.object(self.project_sync, "get_supabase_client", return_value=client),
            patch.object(self.project_sync, "_auth_user_id", return_value=None),
        ):
            found = self.project_sync.fetch_pending_drafts(9)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["draft_id"], "d1")
        self.assertEqual(found[0]["local_scene_id"], 3)
        self.assertEqual(found[0]["content"], "폰에서 씀")
        self.assertEqual(found[0]["based_on_revision_no"], 2)

    def test_mark_draft_merged_updates_status(self) -> None:
        client = SnapshotFakeClient()
        with patch.object(self.project_sync, "get_supabase_client", return_value=client):
            self.project_sync.mark_draft_merged("draft-9")
        self.assertEqual(client.tables["scene_drafts"].updates, [{"status": "merged"}])


class CheckoutSnapshotApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import threading

        import app

        self.app = app
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        supabase_client.reset_supabase_client_cache()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.DATA_DIR = self.original_data_dir
        self.app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()
        supabase_client.reset_supabase_client_cache()

    def test_checkout_schedules_snapshot_then_returns(self) -> None:
        import http.client
        import json as json_mod

        with self.app.database() as connection:
            project_id = int(
                connection.execute(
                    "INSERT INTO project(title) VALUES ('체크아웃작품')"
                ).lastrowid
            )
        scheduled = []

        with (
            patch.object(self.app, "get_supabase_client", return_value=object()),
            patch.object(self.app, "checkout_project"),
            patch.object(
                self.app,
                "schedule_scenes_snapshot_sync",
                side_effect=lambda pid: scheduled.append(pid),
            ),
        ):
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_port
            )
            connection.request(
                "POST",
                f"/api/projects/{project_id}/checkout",
                json.dumps({"title": "체크아웃작품"}).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = json_mod.loads(response.read().decode("utf-8"))
            connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(body, {"ok": True, "id": project_id})
        self.assertEqual(scheduled, [project_id])


class MobileDraftApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import threading

        import app

        self.app = app
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        supabase_client.reset_supabase_client_cache()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.DATA_DIR = self.original_data_dir
        self.app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()
        supabase_client.reset_supabase_client_cache()

    def _request(self, method, path, body=None):
        import http.client
        import json as json_mod

        headers = {"Content-Type": "application/json"} if body is not None else {}
        payload = json_mod.dumps(body).encode("utf-8") if body is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        parsed = json_mod.loads(raw) if raw else None
        return response.status, parsed

    def _seed_scene(self) -> tuple[int, int]:
        with self.app.database() as connection:
            project_id = int(
                connection.execute(
                    "INSERT INTO project(title) VALUES ('초안작품')"
                ).lastrowid
            )
            chapter_id = int(
                connection.execute(
                    "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                    (project_id,),
                ).lastrowid
            )
            scene_id = int(
                connection.execute(
                    "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                    "VALUES (?, ?, '1화', 0)",
                    (project_id, chapter_id),
                ).lastrowid
            )
            connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, is_current) "
                "VALUES (?, 1, '초고', 0)",
                (scene_id,),
            )
            connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, is_current) "
                "VALUES (?, 2, '데스크탑 원고', 1)",
                (scene_id,),
            )
        return project_id, scene_id

    def test_get_mobile_drafts_attaches_title_and_revision(self) -> None:
        project_id, scene_id = self._seed_scene()
        remote = [
            {
                "draft_id": "abc",
                "local_scene_id": scene_id,
                "content": "폰 초안 본문",
                "based_on_revision_no": 1,
                "created_at": "2026-08-21T01:00:00Z",
            }
        ]
        with patch.object(self.app, "fetch_pending_drafts", return_value=remote):
            status, body = self._request("GET", f"/api/projects/{project_id}/mobile-drafts")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["title"], "1화")
        self.assertEqual(body[0]["revision_no"], 2)
        self.assertEqual(body[0]["based_on_revision_no"], 1)
        self.assertEqual(body[0]["content"], "폰 초안 본문")

    def test_merge_mobile_draft_inserts_revision_and_marks_merged(self) -> None:
        _project_id, scene_id = self._seed_scene()
        merged = []
        with patch.object(self.app, "mark_draft_merged", side_effect=lambda did: merged.append(did)):
            status, body = self._request(
                "POST",
                "/api/scene-drafts/abc/merge",
                {"local_scene_id": scene_id, "content": "폰에서 이어서 씀"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["ok"], True)
        self.assertEqual(merged, ["abc"])
        with self.app.database() as connection:
            current = connection.execute(
                "SELECT revision_no, content_md, is_current FROM scene_revision "
                "WHERE scene_id = ? ORDER BY revision_no",
                (scene_id,),
            ).fetchall()
        self.assertEqual(len(current), 3)
        self.assertEqual(int(current[0]["is_current"]), 0)
        self.assertEqual(int(current[1]["is_current"]), 0)
        self.assertEqual(int(current[2]["is_current"]), 1)
        self.assertEqual(int(current[2]["revision_no"]), 3)
        self.assertEqual(current[2]["content_md"], "폰에서 이어서 씀")


if __name__ == "__main__":
    unittest.main()
