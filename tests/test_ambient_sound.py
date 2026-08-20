"""Ambient soundtrack catalog and Korean filename serving."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


FAKE_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00korean-ambient"


class AmbientSoundCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_root = app.AMBIENT_SOUND_ROOT
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        root = Path(self.temporary_directory.name) / "sounds"
        (root / "nature").mkdir(parents=True)
        (root / "ambient").mkdir(parents=True)
        (root / "noise").mkdir(parents=True)
        (root / "frequency").mkdir(parents=True)
        (root / "nature" / "모닥불.mp3").write_bytes(FAKE_MP3)
        (root / "nature" / "시냇물.mp3").write_bytes(FAKE_MP3)
        (root / "nature" / "파도.mp3").write_bytes(FAKE_MP3)
        (root / "ambient" / "밤소리.mp3").write_bytes(FAKE_MP3)
        (root / "ambient" / "창가-비.mp3").write_bytes(FAKE_MP3)
        (root / "ambient" / "카페.mp3").write_bytes(FAKE_MP3)
        (root / "noise" / "White.mp3").write_bytes(FAKE_MP3)
        app.AMBIENT_SOUND_ROOT = root
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
        app.AMBIENT_SOUND_ROOT = self.original_root
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, path: str) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", path)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, raw

    def test_korean_tracks_are_served_over_ascii_urls(self) -> None:
        status, raw = self.request("/api/ambient-tracks")
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode("utf-8"))
        wanted = {
            "nature:모닥불",
            "nature:시냇물",
            "nature:파도",
            "ambient:밤소리",
            "ambient:창가-비",
            "ambient:카페",
        }
        found = {}
        for category in payload["categories"]:
            for track in category["tracks"]:
                found[track["id"]] = track
                self.assertRegex(track["url"], r"^/api/ambient-file/[0-9a-f]+$")
        self.assertTrue(wanted.issubset(found))
        for track_id in wanted:
            status, body = self.request(found[track_id]["url"])
            self.assertEqual(status, 200, track_id)
            self.assertEqual(body, FAKE_MP3)

    def test_resolve_korean_filenames(self) -> None:
        self.assertIsNotNone(app.resolve_ambient_sound_file("nature", "모닥불.mp3"))
        self.assertIsNotNone(app.resolve_ambient_sound_file("nature", "시냇물"))
        self.assertIsNotNone(app.resolve_ambient_sound_file("ambient", "창가-비.mp3"))
        self.assertIsNone(app.resolve_ambient_sound_file("nature", "../noise/White.mp3"))
