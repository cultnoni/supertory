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


if __name__ == "__main__":
    unittest.main()
