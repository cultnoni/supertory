# -*- coding: utf-8 -*-
"""Manual verification for the folder color_bright feature (2nd 쨍한 색 row).

Exercises, against a real temp SQLite DB + real HTTP server (same harness as
tests/test_folder_color_pin.py):
  1) migration adds the column
  2) PATCH color_bright persists and round-trips through GET outline
  3) picking a bright color clears `color` (and vice versa) in ONE undo step
  4) Ctrl+Z (POST /undo) restores the exact previous (color, color_bright) pair
  5) Redo re-applies it
  6) invalid color_bright value is rejected (400)
"""
from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import app
import folder_tree


class FolderColorBrightTests(unittest.TestCase):
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

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        connection.request(
            method,
            path,
            body,
            {"Content-Type": "application/json"} if body else {},
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        result = json.loads(raw) if raw else {}
        connection.close()
        return response.status, result

    def _make_folder(self, title: str) -> tuple[int, int]:
        st, project = self.request(
            "POST", "/api/projects", {"title": title, "main_genre": "판타지"}
        )
        self.assertEqual(st, 201, msg=project)
        pid = int(project["id"])
        st, part = self.request("POST", f"/api/projects/{pid}/parts", {"title": "1권"})
        self.assertEqual(st, 201, msg=part)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fid = folder_tree.folder_id_for_source(conn, pid, "part", int(part["id"]))
        self.assertIsNotNone(fid)
        return pid, fid

    def test_migration_adds_color_bright(self) -> None:
        with app.database() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(folder)").fetchall()}
            self.assertIn("color_bright", cols)
            ver = conn.execute(
                "SELECT 1 FROM schema_migration WHERE version = 34"
            ).fetchone()
            self.assertIsNotNone(ver)

    def test_color_bright_round_trip_and_screens(self) -> None:
        pid, fid = self._make_folder("쨍한색1")

        # Pick a bright swatch (frontend always sends both fields together)
        st, saved = self.request(
            "PUT", f"/api/folders/{fid}", {"color": None, "color_bright": "bright_blue"}
        )
        self.assertEqual(st, 200, msg=saved)
        self.assertIsNone(saved.get("color"))
        self.assertEqual(saved.get("color_bright"), "bright_blue")

        # Screen 1: single-folder response already checked above.
        # Screen 2: outline/tree endpoint (list + tree view source).
        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        roots = outline.get("folders") or []
        self.assertEqual(len(roots), 1)
        self.assertIsNone(roots[0].get("color"))
        self.assertEqual(roots[0].get("color_bright"), "bright_blue")

        # Now switch to a muted color — color_bright must clear.
        st, saved2 = self.request(
            "PUT", f"/api/folders/{fid}", {"color": "green", "color_bright": None}
        )
        self.assertEqual(st, 200, msg=saved2)
        self.assertEqual(saved2.get("color"), "green")
        self.assertIsNone(saved2.get("color_bright"))

        st, outline2 = self.request("GET", f"/api/projects/{pid}/outline")
        roots2 = outline2.get("folders") or []
        self.assertEqual(roots2[0].get("color"), "green")
        self.assertIsNone(roots2[0].get("color_bright"))

    def test_undo_redo_restores_exact_pair(self) -> None:
        pid, fid = self._make_folder("되돌리기")

        # Start: bright_purple
        st, r1 = self.request(
            "PUT", f"/api/folders/{fid}", {"color": None, "color_bright": "bright_purple"}
        )
        self.assertEqual(st, 200, msg=r1)

        # Change: pick a muted color instead (bright_purple -> green, in ONE user action)
        st, r2 = self.request(
            "PUT", f"/api/folders/{fid}", {"color": "green", "color_bright": None}
        )
        self.assertEqual(st, 200, msg=r2)
        self.assertEqual(r2.get("color"), "green")
        self.assertIsNone(r2.get("color_bright"))

        # This whole swatch-pick must be ONE undo entry (atomic).
        with app.database() as conn:
            row = conn.execute(
                "SELECT type FROM folder_action_log WHERE project_id = ? "
                "ORDER BY id DESC LIMIT 2",
                (pid,),
            ).fetchall()
        types = [r[0] for r in row]
        self.assertIn("folder.display_color", types)
        self.assertNotIn("folder.color", types)
        self.assertNotIn("folder.color_bright", types)

        # Ctrl+Z: one undo call must restore color=None, color_bright='bright_purple'
        st, undone = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undone)

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT color, color_bright FROM folder WHERE id = ?", (fid,)
            ).fetchone()
        self.assertIsNone(row["color"])
        self.assertEqual(row["color_bright"], "bright_purple")

        # Redo: back to color=green, color_bright=None
        st, redone = self.request("POST", f"/api/projects/{pid}/redo", {})
        self.assertEqual(st, 200, msg=redone)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT color, color_bright FROM folder WHERE id = ?", (fid,)
            ).fetchone()
        self.assertEqual(row["color"], "green")
        self.assertIsNone(row["color_bright"])

    def test_invalid_color_bright_rejected(self) -> None:
        _pid, fid = self._make_folder("잘못된값")
        st, bad = self.request(
            "PUT", f"/api/folders/{fid}", {"color_bright": "neon_pink"}
        )
        self.assertEqual(st, 400, msg=bad)

    def test_existing_color_untouched(self) -> None:
        """029 color feature must keep working exactly as before (regression guard)."""
        _pid, fid = self._make_folder("기존색상")
        st, saved = self.request("PUT", f"/api/folders/{fid}", {"color": "red"})
        self.assertEqual(st, 200, msg=saved)
        self.assertEqual(saved.get("color"), "red")
        self.assertIsNone(saved.get("color_bright"))
        st, bad = self.request("PUT", f"/api/folders/{fid}", {"color": "neon"})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
