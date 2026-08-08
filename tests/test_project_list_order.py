"""Project list ordering: recent-open default and manual reorder."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app


class ProjectListOrderTests(unittest.TestCase):
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
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        status = response.status
        connection.close()
        return status, result

    def test_recent_open_orders_list_and_manual_reorder(self) -> None:
        status, a = self.request(
            "POST", "/api/projects", {"title": "작품 A", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, a)
        time.sleep(0.02)
        status, b = self.request(
            "POST", "/api/projects", {"title": "작품 B", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, b)
        time.sleep(0.02)
        status, c = self.request(
            "POST", "/api/projects", {"title": "작품 C", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, c)

        status, listed = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual([p["title"] for p in listed], ["작품 C", "작품 B", "작품 A"])
        self.assertEqual(listed[0]["list_mode"], "recent")

        # Opening an older work bumps it to the front.
        status, touch = self.request("POST", f"/api/projects/{a['id']}/touch-open", {})
        self.assertEqual(status, 200)
        self.assertEqual(touch["list_mode"], "recent")
        status, listed = self.request("GET", "/api/projects")
        self.assertEqual([p["title"] for p in listed], ["작품 A", "작품 C", "작품 B"])

        # Manual reorder locks the sequence.
        status, reordered = self.request(
            "PUT",
            "/api/projects/reorder",
            {"project_ids": [b["id"], a["id"], c["id"]]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(reordered["list_mode"], "manual")
        self.assertEqual([p["title"] for p in reordered["projects"]], ["작품 B", "작품 A", "작품 C"])

        status, touch = self.request("POST", f"/api/projects/{c['id']}/touch-open", {})
        self.assertEqual(status, 200)
        self.assertEqual(touch["list_mode"], "manual")
        status, listed = self.request("GET", "/api/projects")
        self.assertEqual([p["title"] for p in listed], ["작품 B", "작품 A", "작품 C"])

        # Restore recent mode after another open of C — C should lead.
        status, restored = self.request("PUT", "/api/projects/list-mode", {"mode": "recent"})
        self.assertEqual(status, 200)
        self.assertEqual(restored["list_mode"], "recent")
        self.assertEqual(restored["projects"][0]["title"], "작품 C")


if __name__ == "__main__":
    unittest.main()
