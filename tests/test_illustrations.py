"""Tests for fairy-tale scene illustrations."""

from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app

# 1x1 PNG
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5WNloAAAAASUVORK5CYII="
)


class IllustrationApiTests(unittest.TestCase):
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
        if path.endswith("/image"):
            return response.status, raw
        return response.status, json.loads(raw.decode("utf-8"))

    def test_create_update_and_delete_illustration_with_overlay(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "별빛 동화", "purpose": "fairy_tale"})
        self.assertEqual(status, 201)
        status, chapter = self.request("POST", f"/api/projects/{project['id']}/chapters", {"title": "1장"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "숲"})
        self.assertEqual(status, 201)

        status, illustration = self.request(
            "POST",
            f"/api/scenes/{scene['id']}/illustrations",
            {
                "mime_type": "image/png",
                "content_base64": base64.b64encode(TINY_PNG).decode("ascii"),
                "caption_md": "숲 속",
                "overlays": [{
                    "id": "ov1",
                    "text": "안녕!",
                    "x": 15,
                    "y": 20,
                    "width": 30,
                    "fontSize": 22,
                    "color": "#8d3e2f",
                    "align": "center",
                }],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(illustration["caption_md"], "숲 속")
        self.assertEqual(illustration["overlays"][0]["text"], "안녕!")
        self.assertTrue(illustration["image_url"].endswith("/image"))

        status, image = self.request("GET", illustration["image_url"])
        self.assertEqual(status, 200)
        self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")

        status, updated = self.request(
            "PUT",
            f"/api/illustrations/{illustration['id']}",
            {
                "caption_md": "밤 숲",
                "overlays": [{
                    "id": "ov1",
                    "text": "잘 자!",
                    "x": 40,
                    "y": 50,
                    "width": 35,
                    "fontSize": 18,
                    "color": "#24211d",
                    "align": "left",
                }],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["caption_md"], "밤 숲")
        self.assertEqual(updated["overlays"][0]["text"], "잘 자!")

        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["project_purpose"], "fairy_tale")
        self.assertEqual(len(detail["illustrations"]), 1)

        status, result = self.request("DELETE", f"/api/illustrations/{illustration['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(result["ok"], True)
        status, listed = self.request("GET", f"/api/scenes/{scene['id']}/illustrations")
        self.assertEqual(status, 200)
        self.assertEqual(listed, [])


if __name__ == "__main__":
    unittest.main()
