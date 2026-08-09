# -*- coding: utf-8 -*-
"""folder.is_bookmarked + U1 undo (folder.bookmark)."""
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


class FolderBookmarkTests(unittest.TestCase):
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

    def _seed(self) -> tuple[int, int]:
        st, project = self.request(
            "POST", "/api/projects", {"title": "Bm", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "부1"}
        )
        self.assertEqual(st, 201)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fid = folder_tree.folder_id_for_source(
                conn, pid, "part", int(part["id"])
            )
            conn.execute(
                "DELETE FROM folder_action_log WHERE project_id = ?",
                (pid,),
            )
            conn.commit()
        self.assertIsNotNone(fid)
        return pid, int(fid)

    def test_migration_031(self) -> None:
        with app.database() as conn:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(folder)").fetchall()
            }
            self.assertIn("is_bookmarked", cols)
            ver = conn.execute(
                "SELECT 1 FROM schema_migration WHERE version = 31"
            ).fetchone()
            self.assertIsNotNone(ver)

    def test_bookmark_round_trip_and_outline(self) -> None:
        pid, fid = self._seed()
        st, before = self.request("PUT", f"/api/folders/{fid}", {"is_bookmarked": True})
        self.assertEqual(st, 200, msg=before)
        self.assertEqual(int(before.get("is_bookmarked") or 0), 1)

        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        folders = outline.get("folders") or []
        self.assertTrue(folders)
        # find bookmarked in forest
        found = False

        def walk(nodes):
            nonlocal found
            for n in nodes or []:
                if int(n.get("id") or 0) == fid:
                    self.assertEqual(int(n.get("is_bookmarked") or 0), 1)
                    found = True
                walk(n.get("children"))

        walk(folders)
        self.assertTrue(found)

        st, after = self.request("PUT", f"/api/folders/{fid}", {"is_bookmarked": False})
        self.assertEqual(st, 200)
        self.assertEqual(int(after.get("is_bookmarked") or 0), 0)

    def test_bookmark_undo(self) -> None:
        pid, fid = self._seed()
        st, _ = self.request("PUT", f"/api/folders/{fid}", {"is_bookmarked": True})
        self.assertEqual(st, 200)
        with app.database() as conn:
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 1)
            top = folder_tree.fetch_undo_stack_top(conn, pid)
            self.assertEqual(
                top["type"] if hasattr(top, "keys") else top[3],
                "folder.bookmark",
            )

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        self.assertEqual(undo.get("type"), "folder.bookmark")
        with app.database() as conn:
            row = conn.execute(
                "SELECT is_bookmarked FROM folder WHERE id = ?", (fid,)
            ).fetchone()
            self.assertEqual(int(row[0] or 0), 0)

        st, redo = self.request("POST", f"/api/projects/{pid}/redo", {})
        self.assertEqual(st, 200, msg=redo)
        with app.database() as conn:
            row = conn.execute(
                "SELECT is_bookmarked FROM folder WHERE id = ?", (fid,)
            ).fetchone()
            self.assertEqual(int(row[0] or 0), 1)


if __name__ == "__main__":
    unittest.main()
