"""HTTP tests for /api/user-settings (primary device + consent)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

import sync.supabase_client as supabase_client


class UserSettingsApiTests(unittest.TestCase):
    def setUp(self) -> None:
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

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        data = json.loads(raw) if raw else {}
        return response.status, data

    def test_get_logged_out_returns_no_settings(self) -> None:
        with patch.object(self.app, "current_user_settings_payload", return_value={
            "settings": None,
            "needs_consent": False,
            "current_device_type": "desktop",
            "user": None,
        }):
            status, body = self._request("GET", "/api/user-settings?device_type=desktop")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIsNone(body["settings"])
        self.assertFalse(body["needs_consent"])

    def test_put_requires_login(self) -> None:
        with patch.object(self.app, "get_current_user", return_value=None):
            status, body = self._request(
                "PUT",
                "/api/user-settings",
                {"primary_device_type": "desktop", "agree": True},
            )
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        self.assertIn("error", body)

    def test_put_saves_primary_device_after_consent(self) -> None:
        saved = {
            "user_id": "u1",
            "primary_device_type": "browser",
            "conflict_policy_agreed_at": "2026-08-28T12:00:00+00:00",
        }
        with (
            patch.object(
                self.app,
                "get_current_user",
                return_value={"id": "u1", "email": "a@example.com"},
            ),
            patch.object(self.app, "get_supabase_client", return_value=object()),
            patch.object(self.app, "save_user_settings", return_value=saved),
        ):
            status, body = self._request(
                "PUT",
                "/api/user-settings",
                {"primary_device_type": "browser", "agree": True},
            )
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(body["ok"])
        self.assertEqual(body["settings"]["primary_device_type"], "browser")
        self.assertTrue(body["settings"]["conflict_policy_agreed_at"])


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeSettingsQuery:
    def __init__(self, store):
        self.store = store
        self._filters = {}
        self._op = "select"
        self._payload = None

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def upsert(self, row, **_kwargs):
        self._op = "upsert"
        self._payload = dict(row)
        return self

    def execute(self):
        if self._op == "upsert":
            uid = self._payload["user_id"]
            self.store[uid] = dict(self._payload)
            return _FakeResult([self.store[uid]])
        uid = self._filters.get("user_id")
        row = self.store.get(uid)
        return _FakeResult([row] if row else [])


class _FakeSettingsClient:
    def __init__(self):
        self.store = {}

    def table(self, _name):
        return _FakeSettingsQuery(self.store)


class UserSettingsStoreTests(unittest.TestCase):
    def test_first_save_requires_agree_then_persists(self) -> None:
        import sync.user_settings as user_settings

        client = _FakeSettingsClient()
        with (
            patch.object(user_settings, "get_supabase_client", return_value=client),
        ):
            with self.assertRaises(ValueError):
                user_settings.save_user_settings("u1", "desktop", agree=False)
            saved = user_settings.save_user_settings("u1", "desktop", agree=True)
        self.assertEqual(saved["primary_device_type"], "desktop")
        self.assertTrue(saved["conflict_policy_agreed_at"])
        self.assertEqual(client.store["u1"]["primary_device_type"], "desktop")

    def test_later_change_does_not_require_reconsent(self) -> None:
        import sync.user_settings as user_settings

        client = _FakeSettingsClient()
        with patch.object(user_settings, "get_supabase_client", return_value=client):
            first = user_settings.save_user_settings("u1", "desktop", agree=True)
            agreed = first["conflict_policy_agreed_at"]
            second = user_settings.save_user_settings("u1", "browser", agree=False)
        self.assertEqual(second["primary_device_type"], "browser")
        self.assertEqual(second["conflict_policy_agreed_at"], agreed)
