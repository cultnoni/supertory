"""Desktop save → Supabase browser_scenes mirror."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app
from services.conflict_resolution_service import HELD_MESSAGE, resolve_write
from services.scene_content_service import SceneContentService
from sync import browser_scene_sync
from sync.browser_scene_sync import (
    SOURCE_TABLE,
    BrowserScenesConflictStore,
    mirror_desktop_scene,
    reset_browser_scene_mirror_cache,
    schedule_browser_scene_mirror,
)

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260828230000_browser_scenes_local_ids.sql"
)


class FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
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

    def limit(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = dict(payload)
        return self

    def upsert(self, payload, on_conflict=None, **_kwargs):
        self._op = "upsert"
        self._on_conflict = on_conflict
        self._payload = dict(payload)
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def execute(self):
        if self._op == "select":
            matches = list(self.store.rows)
            for key, value in self._eq.items():
                matches = [row for row in matches if row.get(key) == value]
            return FakeResult(matches)
        if self._op == "insert":
            row = dict(self._payload)
            self.store.rows.append(row)
            self.store.inserts.append(row)
            return FakeResult([row])
        if self._op == "upsert":
            self.store.upserts.append(
                {"payload": dict(self._payload), "on_conflict": self._on_conflict}
            )
            row = dict(self._payload)
            replaced = False
            for index, existing in enumerate(self.store.rows):
                if existing.get("id") == row.get("id"):
                    merged = dict(existing)
                    merged.update(row)
                    self.store.rows[index] = merged
                    replaced = True
                    row = merged
                    break
            if not replaced:
                self.store.rows.append(row)
            return FakeResult([row])
        if self._op == "update":
            self.store.updates.append(dict(self._payload))
            updated = []
            for index, existing in enumerate(self.store.rows):
                if all(existing.get(key) == value for key, value in self._eq.items()):
                    merged = dict(existing)
                    merged.update(self._payload)
                    self.store.rows[index] = merged
                    updated.append(merged)
            return FakeResult(updated)
        return FakeResult([])


class FakeStore:
    def __init__(self):
        self.rows = []
        self.inserts = []
        self.updates = []
        self.upserts = []


class FakeClient:
    def __init__(self):
        self.tables = {}
        self.table_names_requested = []

    def table(self, name):
        self.table_names_requested.append(name)
        if name not in self.tables:
            self.tables[name] = FakeStore()
        return FakeQuery(self.tables[name])


class BrowserScenesMigrationTests(unittest.TestCase):
    def test_adds_local_ids_and_unique_index(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("add column if not exists local_scene_id integer", sql)
        self.assertIn("add column if not exists local_project_id integer", sql)
        self.assertIn("create unique index if not exists", sql)
        self.assertIn("(user_id, local_scene_id)", sql)
        self.assertIn("where local_scene_id is not null", sql)


class BrowserSceneMirrorTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_browser_scene_mirror_cache()
        self.client = FakeClient()
        self.user_id = "11111111-1111-1111-1111-111111111111"
        self.client.tables["user_settings"] = FakeStore()
        self.client.tables["user_settings"].rows.append(
            {"user_id": self.user_id, "primary_device_type": "desktop"}
        )
        self.client.tables["browser_scenes"] = FakeStore()
        self.client.tables["document_conflict_backups"] = FakeStore()
        self.fixed_now = lambda: datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        reset_browser_scene_mirror_cache()

    def _mirror(self, **overrides):
        kwargs = {
            "local_scene_id": 7,
            "content_html": "데스크톱 원고",
            "title": "1화",
            "local_project_id": 3,
            "user_id": self.user_id,
            "client": self.client,
        }
        kwargs.update(overrides)
        return mirror_desktop_scene(**kwargs)

    def test_skips_when_logged_out(self) -> None:
        with (
            patch.object(browser_scene_sync, "get_current_user", return_value=None),
            patch.object(
                browser_scene_sync, "get_supabase_client", return_value=self.client
            ),
            patch.object(browser_scene_sync, "resolve_write") as writer,
        ):
            result = mirror_desktop_scene(1, "본문", "제목", 2)
        self.assertIsNone(result)
        writer.assert_not_called()
        self.assertEqual(self.client.table_names_requested, [])

    def test_schedule_does_not_start_work_when_logged_out(self) -> None:
        with (
            patch("sync.auth_session.load_session", return_value=None),
            patch.object(browser_scene_sync, "Thread") as thread_cls,
            patch.object(browser_scene_sync, "mirror_desktop_scene") as mirror,
        ):
            schedule_browser_scene_mirror(1, "본문", "제목", 2)
        thread_cls.assert_not_called()
        mirror.assert_not_called()

    def test_inserts_new_row_with_local_ids(self) -> None:
        with patch(
            "services.conflict_resolution_service._utc_now", self.fixed_now
        ):
            result = self._mirror()
        self.assertEqual(result["status"], "saved")
        self.assertFalse(result["conflict"])
        rows = self.client.tables["browser_scenes"].rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["local_scene_id"], 7)
        self.assertEqual(rows[0]["local_project_id"], 3)
        self.assertEqual(rows[0]["user_id"], self.user_id)
        self.assertEqual(rows[0]["title"], "1화")
        self.assertEqual(rows[0]["content_html"], "데스크톱 원고")
        self.assertEqual(rows[0]["row_version"], 1)

    def test_updates_existing_row_by_local_scene_id(self) -> None:
        self.client.tables["browser_scenes"].rows.append(
            {
                "id": "scene-uuid",
                "user_id": self.user_id,
                "local_scene_id": 7,
                "local_project_id": 3,
                "title": "옛 제목",
                "content_html": "옛 본문",
                "row_version": 2,
                "updated_at": "2026-08-28T14:00:00+00:00",
            }
        )
        result = self._mirror(content_html="새 본문", title="새 제목")
        self.assertEqual(result["status"], "saved")
        rows = self.client.tables["browser_scenes"].rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "scene-uuid")
        self.assertEqual(rows[0]["content_html"], "새 본문")
        self.assertEqual(rows[0]["title"], "새 제목")
        self.assertEqual(rows[0]["row_version"], 3)

    def test_resolve_write_called_with_desktop_device(self) -> None:
        seen = []

        def wrapped(*args, **kwargs):
            seen.append((args, kwargs))
            return resolve_write(*args, **kwargs)

        result = self._mirror(resolve_write_fn=wrapped)
        self.assertEqual(result["status"], "saved")
        self.assertEqual(len(seen), 1)
        args = seen[0][0]
        self.assertEqual(args[0], self.user_id)
        self.assertEqual(args[1], SOURCE_TABLE)
        self.assertEqual(args[3], "desktop")
        self.assertIsInstance(args[6], BrowserScenesConflictStore)

    def test_conflict_primary_desktop_overwrites_and_backs_up(self) -> None:
        self.client.tables["browser_scenes"].rows.append(
            {
                "id": "scene-uuid",
                "user_id": self.user_id,
                "local_scene_id": 7,
                "local_project_id": 3,
                "title": "브라우저",
                "content_html": "브라우저본",
                "row_version": 4,
                "updated_at": "2026-08-28T14:30:00+00:00",
            }
        )
        with patch(
            "services.conflict_resolution_service._utc_now", self.fixed_now
        ):
            result = self._mirror(
                content_html="데스크톱본",
                last_known_updated_at="2026-08-28T13:00:00+00:00",
            )
        self.assertEqual(result["status"], "saved")
        self.assertTrue(result["conflict"])
        self.assertEqual(
            self.client.tables["browser_scenes"].rows[0]["content_html"],
            "데스크톱본",
        )
        backups = self.client.tables["document_conflict_backups"].rows
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["source_table"], SOURCE_TABLE)
        self.assertEqual(backups[0]["source_record_id"], "scene-uuid")
        self.assertEqual(backups[0]["content_snapshot"]["content_html"], "브라우저본")

    def test_conflict_non_primary_holds_without_overwrite(self) -> None:
        self.client.tables["user_settings"].rows[0]["primary_device_type"] = "browser"
        self.client.tables["browser_scenes"].rows.append(
            {
                "id": "scene-uuid",
                "user_id": self.user_id,
                "local_scene_id": 7,
                "local_project_id": 3,
                "title": "브라우저",
                "content_html": "브라우저본",
                "row_version": 4,
                "updated_at": "2026-08-28T14:30:00+00:00",
            }
        )
        result = self._mirror(
            content_html="데스크톱본",
            last_known_updated_at="2026-08-28T13:00:00+00:00",
        )
        self.assertEqual(result["status"], "held")
        self.assertFalse(result["saved"])
        self.assertEqual(result["message"], HELD_MESSAGE)
        self.assertEqual(
            self.client.tables["browser_scenes"].rows[0]["content_html"],
            "브라우저본",
        )
        self.assertEqual(self.client.tables["document_conflict_backups"].rows, [])

    def test_missing_user_settings_skips_without_raising(self) -> None:
        self.client.tables["user_settings"].rows.clear()
        result = self._mirror()
        self.assertIsNone(result)
        self.assertEqual(self.client.tables["browser_scenes"].rows, [])


class PersistSceneMirrorHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        with app.database() as connection:
            self.project_id = int(
                connection.execute(
                    "INSERT INTO project(title) VALUES ('미러 검증')"
                ).lastrowid
            )
            chapter_id = int(
                connection.execute(
                    "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                    (self.project_id,),
                ).lastrowid
            )
            self.scene_id = int(
                connection.execute(
                    "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                    "VALUES (?, ?, '1화', 0)",
                    (self.project_id, chapter_id),
                ).lastrowid
            )
            connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
                "VALUES (?, 1, '초고', 1, 1)",
                (self.scene_id,),
            )

    def tearDown(self) -> None:
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _meta(self) -> dict:
        return {
            "title": "1화",
            "status": "draft",
            "synopsis_md": "",
            "notes_md": "",
            "goal_word_count": 0,
            "goal_metric": "chars_with_space",
            "save_note": "저장",
        }

    def test_signed_in_user_id_none_when_logged_out(self) -> None:
        from services.scene_content_service import _signed_in_user_id

        with patch("sync.auth_session.load_session", return_value=None):
            self.assertIsNone(_signed_in_user_id())

    def test_persist_returns_before_cloud_mirror_finishes(self) -> None:
        import time

        hung = []

        def hang_get_user():
            hung.append("called")
            time.sleep(20)
            return {"id": "should-not-block"}

        service = SceneContentService(
            database=app.database,
            word_count=app.word_count,
            parse_reference_links=app.parse_reference_links,
            goal_metrics=app.GOAL_METRICS,
        )
        with (
            patch("sync.auth_session.load_session", return_value=None),
            patch("sync.supabase_client.get_current_user", side_effect=hang_get_user),
        ):
            started = time.perf_counter()
            result = service.persist_scene(
                self.scene_id, "오프라인 저장", self._meta(), 1
            )
            elapsed = time.perf_counter() - started
        self.assertTrue(result["ok"])
        self.assertLess(elapsed, 2.0)
        self.assertEqual(hung, [])

    def test_logged_in_default_path_schedules_mirror(self) -> None:
        scheduled = []
        service = SceneContentService(
            database=app.database,
            word_count=app.word_count,
            parse_reference_links=app.parse_reference_links,
            goal_metrics=app.GOAL_METRICS,
        )
        with (
            patch(
                "services.scene_content_service._signed_in_user_id",
                return_value="11111111-1111-1111-1111-111111111111",
            ),
            patch(
                "sync.browser_scene_sync.schedule_browser_scene_mirror",
                side_effect=lambda *args: scheduled.append(args),
            ),
        ):
            result = service.persist_scene(
                self.scene_id, "로그인 후 저장", self._meta(), 1
            )
        self.assertTrue(result["ok"])
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][0], self.scene_id)
        self.assertEqual(scheduled[0][1], "로그인 후 저장")
        self.assertEqual(scheduled[0][3], self.project_id)

    def test_logged_out_does_not_call_scheduler(self) -> None:
        scheduled = []

        def tracker(*args):
            scheduled.append(args)
            raise AssertionError("로그인하지 않았는데 미러를 시도했습니다.")

        service = SceneContentService(
            database=app.database,
            word_count=app.word_count,
            parse_reference_links=app.parse_reference_links,
            goal_metrics=app.GOAL_METRICS,
        )
        with (
            patch(
                "services.scene_content_service._signed_in_user_id",
                return_value=None,
            ),
            patch(
                "sync.browser_scene_sync.schedule_browser_scene_mirror",
                side_effect=tracker,
            ),
        ):
            result = service.persist_scene(
                self.scene_id, "비가 내렸다.", self._meta(), 1
            )
        self.assertTrue(result["ok"])
        self.assertEqual(scheduled, [])
        with app.database() as connection:
            current = connection.execute(
                "SELECT content_md FROM scene_revision WHERE scene_id = ? AND is_current = 1",
                (self.scene_id,),
            ).fetchone()
        self.assertEqual(current["content_md"], "비가 내렸다.")

    def test_logged_in_mirrors_after_local_save(self) -> None:
        calls = []
        client = FakeClient()
        user_id = "11111111-1111-1111-1111-111111111111"
        client.tables["user_settings"] = FakeStore()
        client.tables["user_settings"].rows.append(
            {"user_id": user_id, "primary_device_type": "desktop"}
        )
        resolve_calls = []

        def hooked(scene_id, content, title, project_id):
            calls.append((scene_id, content, title, project_id))

            def wrapped(*args, **kwargs):
                resolve_calls.append(args[3])
                return resolve_write(*args, **kwargs)

            mirror_desktop_scene(
                scene_id,
                content,
                title,
                project_id,
                user_id=user_id,
                client=client,
                resolve_write_fn=wrapped,
            )

        service = SceneContentService(
            database=app.database,
            word_count=app.word_count,
            parse_reference_links=app.parse_reference_links,
            goal_metrics=app.GOAL_METRICS,
            mirror_after_persist=hooked,
        )
        result = service.persist_scene(
            self.scene_id, "저장될 원고", self._meta(), 1
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            calls,
            [(self.scene_id, "저장될 원고", "1화", self.project_id)],
        )
        self.assertEqual(resolve_calls, ["desktop"])
        rows = client.tables["browser_scenes"].rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["local_scene_id"], self.scene_id)
        self.assertEqual(rows[0]["local_project_id"], self.project_id)
        self.assertEqual(rows[0]["content_html"], "저장될 원고")

    def test_mirror_failure_does_not_fail_local_save(self) -> None:
        def boom(*_args):
            raise RuntimeError("network down")

        service = SceneContentService(
            database=app.database,
            word_count=app.word_count,
            parse_reference_links=app.parse_reference_links,
            goal_metrics=app.GOAL_METRICS,
            mirror_after_persist=boom,
        )
        result = service.persist_scene(
            self.scene_id, "로컬만 살아남음", self._meta(), 1
        )
        self.assertTrue(result["ok"])
        with app.database() as connection:
            current = connection.execute(
                "SELECT content_md FROM scene_revision WHERE scene_id = ? AND is_current = 1",
                (self.scene_id,),
            ).fetchone()
        self.assertEqual(current["content_md"], "로컬만 살아남음")

    def test_default_scheduler_skips_resolve_write_when_logged_out(self) -> None:
        service = SceneContentService(
            database=app.database,
            word_count=app.word_count,
            parse_reference_links=app.parse_reference_links,
            goal_metrics=app.GOAL_METRICS,
        )
        with (
            patch(
                "services.scene_content_service._signed_in_user_id",
                return_value=None,
            ),
            patch.object(browser_scene_sync, "resolve_write") as writer,
            patch.object(
                browser_scene_sync, "get_supabase_client", return_value=object()
            ),
        ):
            result = service.persist_scene(
                self.scene_id, "로컬 전용", self._meta(), 1
            )
        self.assertTrue(result["ok"])
        writer.assert_not_called()
