# -*- coding: utf-8 -*-
"""Folder history redo + redo invalidation on new action."""
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


class FolderUndoRedoTests(unittest.TestCase):
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

    def _project_and_part(self) -> tuple[int, int, int]:
        st, project = self.request(
            "POST", "/api/projects", {"title": "Redo", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "원본"}
        )
        self.assertEqual(st, 201)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            # drain create log for focused tests
            conn.execute(
                """
                UPDATE folder_action_log SET undone_at =
                  strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE project_id = ? AND type = 'folder.create'
                """,
                (pid,),
            )
            # DELETE undone so redo is empty (create drain used mark; clear redo)
            conn.execute(
                "DELETE FROM folder_action_log WHERE project_id = ? AND undone_at IS NOT NULL",
                (pid,),
            )
            conn.commit()
            fid = folder_tree.folder_id_for_source(
                conn, pid, "part", int(part["id"])
            )
        return pid, int(part["id"]), int(fid)

    def test_status_can_redo_after_undo(self) -> None:
        pid, part_id, fid = self._project_and_part()
        st, _ = self.request("PUT", f"/api/parts/{part_id}", {"title": "새이름"})
        self.assertEqual(st, 200)
        st, status = self.request("GET", f"/api/projects/{pid}/undo-status")
        self.assertEqual(st, 200, msg=status)
        self.assertTrue(status.get("can_undo"))
        self.assertFalse(status.get("can_redo"))

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        st, status = self.request("GET", f"/api/projects/{pid}/undo-status")
        self.assertEqual(st, 200, msg=status)
        self.assertFalse(status.get("can_undo"))
        self.assertTrue(status.get("can_redo"))
        self.assertTrue(status.get("redo_label_ko"))

    def test_rename_undo_then_redo(self) -> None:
        pid, part_id, fid = self._project_and_part()
        st, _ = self.request("PUT", f"/api/parts/{part_id}", {"title": "새이름"})
        self.assertEqual(st, 200)
        st, _ = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200)
        with app.database() as conn:
            t = conn.execute(
                "SELECT title FROM folder WHERE id = ?", (fid,)
            ).fetchone()[0]
            self.assertEqual(t, "원본")

        st, redo = self.request("POST", f"/api/projects/{pid}/redo", {})
        self.assertEqual(st, 200, msg=redo)
        self.assertTrue(redo.get("ok"))
        with app.database() as conn:
            t = conn.execute(
                "SELECT title FROM folder WHERE id = ?", (fid,)
            ).fetchone()[0]
            self.assertEqual(t, "새이름")
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 1)
            self.assertEqual(folder_tree.count_undone_action_logs(conn, pid), 0)

    def test_new_action_deletes_redo_stack(self) -> None:
        pid, part_id, fid = self._project_and_part()
        st, _ = self.request("PUT", f"/api/parts/{part_id}", {"title": "A"})
        self.assertEqual(st, 200)
        st, _ = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200)
        with app.database() as conn:
            self.assertEqual(folder_tree.count_undone_action_logs(conn, pid), 1)

        # New action must DELETE undone rows
        st, _ = self.request("PUT", f"/api/parts/{part_id}", {"title": "B"})
        self.assertEqual(st, 200)
        with app.database() as conn:
            self.assertEqual(folder_tree.count_undone_action_logs(conn, pid), 0)

        st, err = self.request("POST", f"/api/projects/{pid}/redo", {})
        self.assertEqual(st, 400, msg=err)

    def test_reparent_undo_redo(self) -> None:
        pid, _, f1 = self._project_and_part()
        st, p2 = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "부2"}
        )
        self.assertEqual(st, 201)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            f2 = folder_tree.folder_id_for_source(
                conn, pid, "part", int(p2["id"])
            )
            conn.execute(
                "DELETE FROM folder_action_log WHERE project_id = ?",
                (pid,),
            )
            conn.commit()

        st, r = self.request(
            "POST",
            f"/api/folders/{f2}/reparent",
            {"new_parent_id": f1, "position": "inside", "target_id": f1},
        )
        self.assertEqual(st, 200, msg=r)
        st, _ = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200)
        with app.database() as conn:
            parent = conn.execute(
                "SELECT parent_id FROM folder WHERE id = ?", (f2,)
            ).fetchone()[0]
            self.assertIsNone(parent)

        st, redo = self.request("POST", f"/api/projects/{pid}/redo", {})
        self.assertEqual(st, 200, msg=redo)
        with app.database() as conn:
            parent = conn.execute(
                "SELECT parent_id FROM folder WHERE id = ?", (f2,)
            ).fetchone()[0]
            self.assertEqual(int(parent), f1)


if __name__ == "__main__":
    unittest.main()
