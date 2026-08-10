"""Writing-day clear API: all / selected days."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class WritingLogClearTests(unittest.TestCase):
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
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _seed_days(self) -> None:
        self.request("POST", "/api/writing/heartbeat", {
            "day": "2026-01-01",
            "chars_delta": 100,
            "active_seconds_delta": 60,
            "session_start": True,
        })
        self.request("POST", "/api/writing/heartbeat", {
            "day": "2026-01-02",
            "chars_delta": 50,
            "active_seconds_delta": 30,
            "session_start": True,
        })
        self.request("POST", "/api/writing/heartbeat", {
            "day": "2026-01-03",
            "chars_delta": 20,
            "active_seconds_delta": 10,
            "session_start": True,
        })

    def test_clear_selected_days(self) -> None:
        self._seed_days()
        status, listed = self.request("GET", "/api/writing/days?from=2026-01-01&to=2026-01-03")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["days"]), 3)

        status, cleared = self.request("POST", "/api/writing/days/clear", {
            "days": ["2026-01-01", "2026-01-03"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(cleared["deleted"], 2)
        self.assertEqual(sorted(cleared["days"]), ["2026-01-01", "2026-01-03"])

        status, listed = self.request("GET", "/api/writing/days?from=2026-01-01&to=2026-01-03")
        self.assertEqual(status, 200)
        self.assertEqual([d["day"] for d in listed["days"]], ["2026-01-02"])

    def test_clear_all_days(self) -> None:
        self._seed_days()
        status, cleared = self.request("POST", "/api/writing/days/clear", {"all": True})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(cleared["deleted"], 3)
        status, listed = self.request("GET", "/api/writing/days?from=2026-01-01&to=2026-01-03")
        self.assertEqual(status, 200)
        self.assertEqual(listed["days"], [])


if __name__ == "__main__":
    unittest.main()
