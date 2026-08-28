"""Primary-device conflict resolver and RLS policy contract tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from services.conflict_resolution_service import (
    DEVICE_TYPES,
    HELD_MESSAGE,
    MissingUserSettings,
    resolve_write,
)

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260828120000_user_settings_and_conflict_backups.sql"
)


class InMemoryConflictStore:
    def __init__(self) -> None:
        self.settings: dict[str, dict] = {}
        self.records: dict[tuple[str, str], dict] = {}
        self.backups: list[dict] = []

    def get_user_settings(self, user_id: str) -> dict | None:
        return self.settings.get(user_id)

    def get_record(self, source_table: str, record_id: str) -> dict | None:
        row = self.records.get((source_table, record_id))
        return dict(row) if row else None

    def save_record(
        self,
        source_table: str,
        record_id: str,
        content: object,
        updated_at: str,
        device_type: str,
    ) -> None:
        self.records[(source_table, record_id)] = {
            "content": content,
            "updated_at": updated_at,
            "device_type": device_type,
        }

    def insert_conflict_backup(self, payload: dict) -> dict:
        row = dict(payload)
        self.backups.append(row)
        return row


class RlsAwareTable:
    """In-memory stand-in for `auth.uid() = user_id` RLS."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert(self, uid: str | None, row: dict) -> dict | None:
        if uid is None or row.get("user_id") != uid:
            return None
        self.rows.append(dict(row))
        return row

    def select(self, uid: str | None) -> list[dict]:
        if uid is None:
            return []
        return [dict(row) for row in self.rows if row.get("user_id") == uid]


class ConflictResolutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryConflictStore()
        self.user_id = "user-1"
        self.store.settings[self.user_id] = {"primary_device_type": "desktop"}
        self.fixed_now = lambda: datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)

    def _seed(self, updated_at: str, content: str, device_type: str = "browser") -> None:
        self.store.records[("scenes", "scene-1")] = {
            "updated_at": updated_at,
            "content": content,
            "device_type": device_type,
        }

    def test_no_conflict_saves_normally(self) -> None:
        self._seed("2026-08-28T10:00:00+00:00", "서버본")
        result = resolve_write(
            self.user_id,
            "scenes",
            "scene-1",
            "mobile",
            "새본",
            "2026-08-28T10:00:00+00:00",
            self.store,
            now=self.fixed_now,
        )
        self.assertEqual(result["status"], "saved")
        self.assertFalse(result["conflict"])
        self.assertEqual(self.store.records[("scenes", "scene-1")]["content"], "새본")
        self.assertEqual(self.store.backups, [])

    def test_conflict_primary_device_overwrites_and_backs_up(self) -> None:
        self._seed("2026-08-28T11:00:00+00:00", "브라우저본", "browser")
        result = resolve_write(
            self.user_id,
            "scenes",
            "scene-1",
            "desktop",
            "데스크톱본",
            "2026-08-28T10:00:00+00:00",
            self.store,
            now=self.fixed_now,
        )
        self.assertEqual(result["status"], "saved")
        self.assertTrue(result["conflict"])
        self.assertEqual(self.store.records[("scenes", "scene-1")]["content"], "데스크톱본")
        self.assertEqual(len(self.store.backups), 1)
        backup = self.store.backups[0]
        self.assertEqual(backup["user_id"], self.user_id)
        self.assertEqual(backup["source_table"], "scenes")
        self.assertEqual(backup["source_record_id"], "scene-1")
        self.assertEqual(backup["losing_device_type"], "browser")
        self.assertEqual(backup["content_snapshot"], "브라우저본")
        self.assertEqual(result["backup_id"], backup["id"])

    def test_conflict_non_primary_holds_without_save(self) -> None:
        self._seed("2026-08-28T11:00:00+00:00", "데스크톱본", "desktop")
        result = resolve_write(
            self.user_id,
            "scenes",
            "scene-1",
            "browser",
            "브라우저본",
            "2026-08-28T10:00:00+00:00",
            self.store,
            now=self.fixed_now,
        )
        self.assertEqual(result["status"], "held")
        self.assertTrue(result["conflict"])
        self.assertFalse(result["saved"])
        self.assertEqual(result["message"], HELD_MESSAGE)
        self.assertEqual(self.store.records[("scenes", "scene-1")]["content"], "데스크톱본")
        self.assertEqual(self.store.backups, [])

    def test_conflict_non_primary_force_saves_and_backs_up(self) -> None:
        self._seed("2026-08-28T11:00:00+00:00", "데스크톱본", "desktop")
        result = resolve_write(
            self.user_id,
            "scenes",
            "scene-1",
            "browser",
            "브라우저본",
            "2026-08-28T10:00:00+00:00",
            self.store,
            force=True,
            now=self.fixed_now,
        )
        self.assertEqual(result["status"], "saved")
        self.assertTrue(result["forced"])
        self.assertEqual(self.store.records[("scenes", "scene-1")]["content"], "브라우저본")
        self.assertEqual(self.store.backups[0]["content_snapshot"], "데스크톱본")
        self.assertEqual(self.store.backups[0]["losing_device_type"], "desktop")

    def test_new_record_is_not_a_conflict(self) -> None:
        result = resolve_write(
            self.user_id,
            "translation_segments",
            "seg-1",
            "browser",
            {"text": "hello"},
            None,
            self.store,
            now=self.fixed_now,
        )
        self.assertFalse(result["conflict"])
        self.assertEqual(
            self.store.records[("translation_segments", "seg-1")]["content"],
            {"text": "hello"},
        )
        self.assertEqual(self.store.backups, [])

    def test_requires_onboarding_settings(self) -> None:
        with self.assertRaises(MissingUserSettings):
            resolve_write(
                "nobody",
                "scenes",
                "scene-1",
                "desktop",
                "본문",
                None,
                self.store,
            )


class ConflictRlsTests(unittest.TestCase):
    def test_migration_enables_owner_only_rls(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("create table if not exists public.user_settings", sql)
        self.assertIn("create table if not exists public.document_conflict_backups", sql)
        self.assertIn("enable row level security", sql)
        self.assertIn("auth.uid() = user_id", sql)
        self.assertIn("user_settings_select_own", sql)
        self.assertIn("document_conflict_backups_select_own", sql)
        self.assertIn("to authenticated", sql)
        for device in DEVICE_TYPES:
            self.assertIn(f"'{device}'", sql)

    def test_rls_hides_other_users_settings_and_backups(self) -> None:
        settings = RlsAwareTable()
        backups = RlsAwareTable()
        alice = "11111111-1111-1111-1111-111111111111"
        bob = "22222222-2222-2222-2222-222222222222"
        self.assertIsNotNone(
            settings.insert(
                alice,
                {"user_id": alice, "primary_device_type": "desktop"},
            )
        )
        self.assertIsNone(
            settings.insert(
                bob,
                {"user_id": alice, "primary_device_type": "mobile"},
            )
        )
        self.assertIsNotNone(
            backups.insert(
                alice,
                {
                    "user_id": alice,
                    "source_table": "scenes",
                    "content_snapshot": {"text": "alice"},
                },
            )
        )
        self.assertEqual(len(settings.select(alice)), 1)
        self.assertEqual(settings.select(bob), [])
        self.assertEqual(len(backups.select(alice)), 1)
        self.assertEqual(backups.select(bob), [])
        self.assertEqual(settings.select(None), [])
        self.assertEqual(backups.select(None), [])
