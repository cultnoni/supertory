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

    def test_default_room_is_exclusive_and_persists(self) -> None:
        self.request("POST", "/api/gitsi/rooms", {"room_code": "supertory-alpha"})
        self.request("POST", "/api/gitsi/rooms", {"room_code": "supertory-beta"})

        status, first = self.request(
            "POST",
            "/api/gitsi/rooms/default",
            {"room_code": "supertory-alpha"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(first["room_code"], "supertory-alpha")
        self.assertEqual(int(first["is_default"]), 1)

        status, second = self.request(
            "POST",
            "/api/gitsi/rooms/default",
            {"room_code": "supertory-beta"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(second["room_code"], "supertory-beta")
        self.assertEqual(int(second["is_default"]), 1)

        status, listed = self.request("GET", "/api/gitsi/rooms")
        self.assertEqual(status, 200)
        by_code = {row["room_code"]: int(row["is_default"]) for row in listed["rooms"]}
        self.assertEqual(by_code["supertory-beta"], 1)
        self.assertEqual(by_code["supertory-alpha"], 0)
        self.assertEqual(sum(by_code.values()), 1)

        status, current = self.request("GET", "/api/gitsi/rooms/default")
        self.assertEqual(status, 200)
        self.assertEqual(current["room"]["room_code"], "supertory-beta")

        app.initialise_database()
        status, after = self.request("GET", "/api/gitsi/rooms/default")
        self.assertEqual(status, 200)
        self.assertEqual(after["room"]["room_code"], "supertory-beta")

        status, cleared = self.request(
            "POST",
            "/api/gitsi/rooms/default",
            {"room_code": "supertory-beta", "is_default": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(int(cleared["is_default"]), 0)
        status, empty = self.request("GET", "/api/gitsi/rooms/default")
        self.assertEqual(status, 200)
        self.assertIsNone(empty["room"])

    def test_deactivate_room_hides_from_list(self) -> None:
        self.request("POST", "/api/gitsi/rooms", {"room_code": "supertory-keep"})
        self.request("POST", "/api/gitsi/rooms", {"room_code": "supertory-drop"})
        self.request(
            "POST",
            "/api/gitsi/rooms/default",
            {"room_code": "supertory-drop"},
        )

        status, deleted = self.request("DELETE", "/api/gitsi/rooms/supertory-drop")
        self.assertEqual(status, 200)
        self.assertEqual(deleted["ok"], True)
        self.assertEqual(deleted["room"]["room_code"], "supertory-drop")
        self.assertEqual(int(deleted["room"]["is_active"]), 0)
        self.assertEqual(int(deleted["room"]["is_default"]), 0)

        status, listed = self.request("GET", "/api/gitsi/rooms")
        self.assertEqual(status, 200)
        codes = [row["room_code"] for row in listed["rooms"]]
        self.assertIn("supertory-keep", codes)
        self.assertNotIn("supertory-drop", codes)

        status, current = self.request("GET", "/api/gitsi/rooms/default")
        self.assertEqual(status, 200)
        self.assertIsNone(current["room"])

        status, rejoined = self.request("POST", "/api/gitsi/rooms", {"room_code": "supertory-drop"})
        self.assertEqual(status, 200)
        self.assertFalse(rejoined["created"])
        self.assertEqual(int(rejoined["is_active"]), 1)

        status, listed_again = self.request("GET", "/api/gitsi/rooms")
        self.assertEqual(status, 200)
        self.assertIn("supertory-drop", [row["room_code"] for row in listed_again["rooms"]])

        status, missing = self.request("DELETE", "/api/gitsi/rooms/supertory-missing")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
