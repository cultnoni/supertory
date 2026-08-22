"""API tests for Gitsi coworking rooms."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class GitsiRoomsApiTests(unittest.TestCase):
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

    def test_create_join_and_list_rooms(self) -> None:
        status, suggested = self.request("GET", "/api/gitsi/suggest-code")
        self.assertEqual(status, 200)
        self.assertTrue(str(suggested["room_code"]).startswith("supertory-"))

        status, created = self.request(
            "POST",
            "/api/gitsi/rooms",
            {"room_code": "supertory-test", "created_by": "writer@example.com"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["room_code"], "supertory-test")
        self.assertTrue(created["created"])
        self.assertEqual(created["created_by"], "writer@example.com")
        self.assertEqual(int(created["is_active"]), 1)

        status, joined = self.request("POST", "/api/gitsi/rooms", {"room_code": "supertory-test"})
        self.assertEqual(status, 200)
        self.assertFalse(joined["created"])
        self.assertEqual(joined["room_code"], "supertory-test")

        status, listed = self.request("GET", "/api/gitsi/rooms")
        self.assertEqual(status, 200)
        codes = [row["room_code"] for row in listed["rooms"]]
        self.assertIn("supertory-test", codes)

        status, generated = self.request("POST", "/api/gitsi/rooms", {})
        self.assertEqual(status, 201)
        self.assertTrue(str(generated["room_code"]).startswith("supertory-"))
        self.assertTrue(generated["created"])

    def test_rejects_invalid_room_code(self) -> None:
        status, result = self.request("POST", "/api/gitsi/rooms", {"room_code": "??"})
        self.assertEqual(status, 400)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
