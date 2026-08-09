# -*- coding: utf-8 -*-
"""U1: folder_action_log + undo for rename/color/box/pin."""
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


class FolderUndoU1Tests(unittest.TestCase):
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

    def _seed_part_folder(self) -> tuple[int, int, int]:
        st, project = self.request(
            "POST", "/api/projects", {"title": "UndoU1", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "원본이름"}
        )
        self.assertEqual(st, 201)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fid = folder_tree.folder_id_for_source(
                conn, pid, "part", int(part["id"])
            )
            # U3 logs folder.create on part create — remove so U1 tests see only patch logs
            # (DELETE, not mark undone — otherwise can_redo becomes true)
            conn.execute(
                """
                DELETE FROM folder_action_log
                WHERE project_id = ? AND type = 'folder.create'
                """,
                (pid,),
            )
            conn.commit()
        self.assertIsNotNone(fid)
        return pid, int(part["id"]), int(fid)

    def test_migration_030(self) -> None:
        with app.database() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("folder_action_log", tables)
            ver = conn.execute(
                "SELECT 1 FROM schema_migration WHERE version = 30"
            ).fetchone()
            self.assertIsNotNone(ver)
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='ix_folder_action_log_stack'"
            ).fetchone()
            self.assertIsNotNone(idx)

    def test_undo_status_endpoint(self) -> None:
        pid, part_id, _fid = self._seed_part_folder()
        st, status = self.request("GET", f"/api/projects/{pid}/undo-status")
        self.assertEqual(st, 200, msg=status)
        self.assertFalse(status.get("can_undo"))
        self.assertFalse(status.get("can_redo"))

        st, _ = self.request("PUT", f"/api/parts/{part_id}", {"title": "상태검사용"})
        self.assertEqual(st, 200)
        st, status = self.request("GET", f"/api/projects/{pid}/undo-status")
        self.assertEqual(st, 200, msg=status)
        self.assertTrue(status.get("can_undo"))
        self.assertFalse(status.get("can_redo"))
        self.assertEqual(status.get("type"), "folder.rename")
        self.assertTrue(status.get("label_ko"))

    def test_rename_undo(self) -> None:
        pid, part_id, fid = self._seed_part_folder()
        st, _ = self.request(
            "PUT", f"/api/parts/{part_id}", {"title": "새이름"}
        )
        self.assertEqual(st, 200)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            t = conn.execute(
                "SELECT title FROM folder WHERE id = ?", (fid,)
            ).fetchone()["title"]
            self.assertEqual(t, "새이름")
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 1)

        st, result = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=result)
        self.assertTrue(result.get("ok"))
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            t = conn.execute(
                "SELECT title FROM folder WHERE id = ?", (fid,)
            ).fetchone()["title"]
            self.assertEqual(t, "원본이름")
            pt = conn.execute(
                "SELECT title FROM part WHERE id = ?", (part_id,)
            ).fetchone()["title"]
            self.assertEqual(pt, "원본이름")
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 0)

    def test_color_undo(self) -> None:
        pid, _part_id, fid = self._seed_part_folder()
        st, _ = self.request("PUT", f"/api/folders/{fid}", {"color": "green"})
        self.assertEqual(st, 200)
        st, result = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=result)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            c = conn.execute(
                "SELECT color FROM folder WHERE id = ?", (fid,)
            ).fetchone()["color"]
            self.assertIsNone(c)

    def test_box_and_pin_undo(self) -> None:
        pid, _part_id, fid = self._seed_part_folder()
        # parts start as is_box=1 typically
        st, before = self.request("PUT", f"/api/folders/{fid}", {"is_box": False})
        self.assertEqual(st, 200)
        self.assertEqual(int(before.get("is_box") or 0), 0)
        st, r1 = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=r1)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            b = conn.execute(
                "SELECT is_box FROM folder WHERE id = ?", (fid,)
            ).fetchone()["is_box"]
            self.assertEqual(int(b), 1)

        st, _ = self.request("PUT", f"/api/folders/{fid}", {"is_pinned": True})
        self.assertEqual(st, 200)
        st, r2 = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=r2)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            p = conn.execute(
                "SELECT is_pinned FROM folder WHERE id = ?", (fid,)
            ).fetchone()["is_pinned"]
            self.assertEqual(int(p), 0)

    def test_conflict_skips_entry(self) -> None:
        pid, _part_id, fid = self._seed_part_folder()
        st, _ = self.request("PUT", f"/api/folders/{fid}", {"color": "blue"})
        self.assertEqual(st, 200)
        # Third value
        st, _ = self.request("PUT", f"/api/folders/{fid}", {"color": "red"})
        self.assertEqual(st, 200)
        # Undo top is red←blue; current is red = forward.new for second entry
        # First undo: red -> blue OK
        st, r1 = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=r1)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            c = conn.execute(
                "SELECT color FROM folder WHERE id = ?", (fid,)
            ).fetchone()["color"]
            self.assertEqual(c, "blue")

        # Manually set third value while stack has blue←None (or green)
        # Stack top: first action was None→blue
        # Current is blue = forward.new → undo should set None
        # Instead set green before undo to conflict
        st, _ = self.request("PUT", f"/api/folders/{fid}", {"color": "green"})
        self.assertEqual(st, 200)
        # Stack: green←blue (new), and blue←None (old). Top is green←blue
        # Undo top: current green == new → back to blue
        st, r2 = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=r2)
        # Now top is blue←None but current is blue... wait after undo current is blue
        # Actually after r2 current is blue. Top is first entry None→blue.
        # Set yellow for conflict
        st, _ = self.request("PUT", f"/api/folders/{fid}", {"color": "yellow"})
        # Top is still None→blue (active). Current yellow is third value relative to that entry.
        # But we also pushed yellow←blue. So top is yellow←blue.
        # Undo: yellow→blue OK. Then top None→blue, set pink for conflict.
        st, r3 = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200)
        # force conflict on remaining None→blue: set orange
        st, _ = self.request("PUT", f"/api/folders/{fid}", {"color": "orange"})
        # This logs orange←blue. Top is orange←blue.
        # We need conflict on entry where new=blue old=None but current=orange:
        # Manually mark to only leave the original entry... simpler approach:

        # Fresh: clear by undoing all then one color and conflict
        while True:
            st, r = self.request("POST", f"/api/projects/{pid}/undo", {})
            if st != 200:
                break

        st, _ = self.request("PUT", f"/api/folders/{fid}", {"color": "purple"})
        # third value without logging via raw SQL
        with app.database() as conn:
            conn.execute(
                "UPDATE folder SET color = 'gray' WHERE id = ?", (fid,)
            )
            conn.commit()
            active_before = folder_tree.count_active_action_logs(conn, pid)
            self.assertEqual(active_before, 1)

        st, err = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 400, msg=err)
        self.assertIn("error", err)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            # entry skipped (undone_at set)
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 0)
            c = conn.execute(
                "SELECT color FROM folder WHERE id = ?", (fid,)
            ).fetchone()["color"]
            self.assertEqual(c, "gray")  # third value kept

    def test_purge_keeps_20(self) -> None:
        pid, _part_id, fid = self._seed_part_folder()
        colors = ["red", "orange", "yellow", "green", "blue", "purple", "gray"]
        for i in range(25):
            c = colors[i % len(colors)]
            st, _ = self.request(
                "PUT", f"/api/folders/{fid}", {"color": c}
            )
            self.assertEqual(st, 200)
        with app.database() as conn:
            n = folder_tree.count_active_action_logs(conn, pid)
            self.assertLessEqual(n, 20)
            self.assertEqual(n, 20)


if __name__ == "__main__":
    unittest.main()
