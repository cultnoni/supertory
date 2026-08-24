"""Ambient soundtrack catalog and Korean filename serving."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from urllib.parse import quote

import app


FAKE_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00korean-ambient"


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = "----SuperToryAmbientTestBoundary"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    for name, (filename, data) in files.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class AmbientSoundCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_root = app.AMBIENT_SOUND_ROOT
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        self.original_transcode = app.transcode_to_ambient_mp3
        self.original_probe = app.probe_audio_duration_seconds
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
        app.transcode_to_ambient_mp3 = self.original_transcode
        app.probe_audio_duration_seconds = self.original_probe
        self.temporary_directory.cleanup()

    def request(
        self,
        path: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict | None = None,
    ) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, raw

    def install_fake_ffmpeg(self, duration: float = 12.0, output: bytes = FAKE_MP3) -> None:
        def transcode(_source: Path, destination: Path) -> None:
            destination.write_bytes(output)

        def probe(_path: Path) -> float:
            return float(duration)

        app.transcode_to_ambient_mp3 = transcode
        app.probe_audio_duration_seconds = probe

    def insert_custom_track(
        self,
        *,
        category: str = "custom",
        original_filename: str = "빗소리.wav",
        data: bytes = FAKE_MP3,
        duration: float = 8.0,
        size: int | None = None,
    ) -> tuple[int, str]:
        stored = f"{uuid.uuid4().hex}.mp3"
        path = app.ambient_custom_dir() / stored
        path.write_bytes(data)
        with app.database() as connection:
            cursor = connection.execute(
                "INSERT INTO user_ambient_tracks("
                "original_filename, stored_filename, duration_seconds, file_size_bytes, category) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    original_filename,
                    stored,
                    duration,
                    size if size is not None else len(data),
                    category,
                ),
            )
            track_id = int(cursor.lastrowid)
        return track_id, stored

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

    def test_custom_tracks_are_listed_and_served(self) -> None:
        track_id, stored = self.insert_custom_track()
        status, raw = self.request("/api/ambient-tracks")
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode("utf-8"))
        self.assertIn("usage", payload)
        self.assertEqual(payload["categories"][-1]["id"], "custom")
        custom_cat = next(cat for cat in payload["categories"] if cat["id"] == "custom")
        custom = next(track for track in custom_cat["tracks"] if track.get("custom"))
        self.assertEqual(custom["id"], f"custom:{track_id}")
        self.assertEqual(custom["category"], "custom")
        self.assertEqual(custom["file"], "빗소리.wav")
        self.assertTrue(custom["custom"])
        for builtin in ("frequency", "noise", "nature", "ambient"):
            cat = next(item for item in payload["categories"] if item["id"] == builtin)
            self.assertFalse(any(track.get("custom") for track in cat["tracks"]))
        status, body = self.request(custom["url"])
        self.assertEqual(status, 200)
        self.assertEqual(body, FAKE_MP3)
        self.assertIsNotNone(app.resolve_ambient_sound_file("custom", stored))
        self.assertIsNone(app.resolve_ambient_sound_file("custom", "../x.mp3"))

    def test_legacy_custom_tracks_move_into_custom_style(self) -> None:
        track_id, _stored = self.insert_custom_track(category="nature")
        status, raw = self.request("/api/ambient-tracks")
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode("utf-8"))
        nature = next(cat for cat in payload["categories"] if cat["id"] == "nature")
        self.assertFalse(any(track.get("custom") for track in nature["tracks"]))
        custom_cat = next(cat for cat in payload["categories"] if cat["id"] == "custom")
        custom = next(track for track in custom_cat["tracks"] if track["id"] == f"custom:{track_id}")
        self.assertEqual(custom["category"], "custom")

    def test_upload_custom_track_reencodes_and_enforces_limits(self) -> None:
        self.install_fake_ffmpeg(duration=9.5, output=FAKE_MP3 * 8)
        body, content_type = _multipart(
            {"category": "custom"},
            {"file": ("brown-noise.wav", b"RIFF" + b"\x00" * 64)},
        )
        status, raw = self.request(
            "/api/ambient/upload",
            "POST",
            body,
            {"Content-Type": content_type},
        )
        self.assertEqual(status, 201, raw.decode("utf-8", errors="replace"))
        payload = json.loads(raw.decode("utf-8"))
        track = payload["track"]
        self.assertEqual(track["category"], "custom")
        self.assertTrue(track["custom"])
        self.assertEqual(track["file"], "brown-noise.wav")
        with app.database() as connection:
            row = connection.execute(
                "SELECT stored_filename, duration_seconds FROM user_ambient_tracks WHERE id = ?",
                (track["custom_id"],),
            ).fetchone()
        self.assertTrue(str(row["stored_filename"]).endswith(".mp3"))
        self.assertAlmostEqual(float(row["duration_seconds"]), 9.5)
        self.assertTrue((app.ambient_custom_dir() / row["stored_filename"]).is_file())

        self.install_fake_ffmpeg(duration=21 * 60)
        body, content_type = _multipart(
            {"category": "nature"},
            {"file": ("too-long.mp3", b"ID3long")},
        )
        status, raw = self.request(
            "/api/ambient/upload",
            "POST",
            body,
            {"Content-Type": content_type},
        )
        self.assertEqual(status, 400)
        self.assertIn("짧은 루프", json.loads(raw.decode("utf-8"))["error"])

        body, content_type = _multipart(
            {"category": "nature"},
            {"file": ("notes.txt", b"hello")},
        )
        status, raw = self.request(
            "/api/ambient/upload",
            "POST",
            body,
            {"Content-Type": content_type},
        )
        self.assertEqual(status, 400)
        self.assertIn("mp3", json.loads(raw.decode("utf-8"))["error"])

        self.insert_custom_track(size=app.CUSTOM_AMBIENT_QUOTA_BYTES - 10, data=b"x")
        self.install_fake_ffmpeg(duration=3.0, output=b"ID3" + b"\x00" * 64)
        body, content_type = _multipart(
            {"category": "ambient"},
            {"file": ("cafe-extra.wav", b"RIFF-extra")},
        )
        status, raw = self.request(
            "/api/ambient/upload",
            "POST",
            body,
            {"Content-Type": content_type},
        )
        self.assertEqual(status, 400)
        self.assertIn("400MB", json.loads(raw.decode("utf-8"))["error"])

    def test_delete_custom_track_removes_file_and_row(self) -> None:
        track_id, stored = self.insert_custom_track()
        path = app.ambient_custom_dir() / stored
        self.assertTrue(path.is_file())
        status, raw = self.request(f"/api/ambient/custom/{track_id}", "DELETE")
        self.assertEqual(status, 200, raw.decode("utf-8", errors="replace"))
        self.assertFalse(path.exists())
        with app.database() as connection:
            row = connection.execute(
                "SELECT id FROM user_ambient_tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
        self.assertIsNone(row)
        with app.database() as connection:
            leftover = connection.execute(
                "SELECT track_id FROM ambient_track_overrides WHERE track_id = ?",
                (f"custom:{track_id}",),
            ).fetchone()
        self.assertIsNone(leftover)

    def _patch_override(self, track_id: str, payload: dict) -> tuple[int, dict]:
        status, raw = self.request(
            f"/api/ambient/overrides/{quote(track_id, safe='')}",
            "PATCH",
            json.dumps(payload).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        return status, json.loads(raw.decode("utf-8"))

    def test_catalog_includes_override_defaults(self) -> None:
        status, raw = self.request("/api/ambient-tracks")
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode("utf-8"))
        nature = next(cat for cat in payload["categories"] if cat["id"] == "nature")
        campfire = next(track for track in nature["tracks"] if track["id"] == "nature:모닥불")
        self.assertEqual(campfire["display_title"], "모닥불")
        self.assertIsNone(campfire["custom_title"])
        self.assertTrue(campfire["enabled_in_popup"])

    def test_patch_override_renames_and_hides_from_popup_flag(self) -> None:
        status, body = self._patch_override("nature:모닥불", {"custom_title": "모닥불 루프"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["custom_title"], "모닥불 루프")
        self.assertEqual(body["display_title"], "모닥불 루프")
        self.assertTrue(body["enabled_in_popup"])

        status, body = self._patch_override("nature:모닥불", {"enabled_in_popup": False})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["custom_title"], "모닥불 루프")
        self.assertFalse(body["enabled_in_popup"])

        status, raw = self.request("/api/ambient-tracks")
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode("utf-8"))
        nature = next(cat for cat in payload["categories"] if cat["id"] == "nature")
        campfire = next(track for track in nature["tracks"] if track["id"] == "nature:모닥불")
        self.assertEqual(campfire["display_title"], "모닥불 루프")
        self.assertFalse(campfire["enabled_in_popup"])

        custom_id, _stored = self.insert_custom_track()
        custom_key = f"custom:{custom_id}"
        status, body = self._patch_override(custom_key, {"custom_title": "비 오는 밤"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["display_title"], "비 오는 밤")
        status, raw = self.request("/api/ambient-tracks")
        payload = json.loads(raw.decode("utf-8"))
        custom_cat = next(cat for cat in payload["categories"] if cat["id"] == "custom")
        custom = next(track for track in custom_cat["tracks"] if track["id"] == custom_key)
        self.assertEqual(custom["display_title"], "비 오는 밤")
        self.assertTrue(custom["enabled_in_popup"])


if __name__ == "__main__":
    unittest.main()
