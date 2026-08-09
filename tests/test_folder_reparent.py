# -*- coding: utf-8 -*-
"""Phase 2-a: POST /api/folders/{id}/reparent + outline force-folder rules."""
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


class FolderReparentTests(unittest.TestCase):
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

    def _folder_id_for_part(self, project_id: int, part_id: int) -> int:
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fid = folder_tree.folder_id_for_source(
                conn, int(project_id), "part", int(part_id)
            )
            self.assertIsNotNone(fid)
            return int(fid)

    def _folder_row(self, folder_id: int) -> sqlite3.Row:
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM folder WHERE id = ? AND deleted_at IS NULL",
                (int(folder_id),),
            ).fetchone()
            self.assertIsNotNone(row)
            return row

    def test_reparent_inside_nests_part_under_part(self) -> None:
        st, project = self.request(
            "POST", "/api/projects", {"title": "리페런트", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])

        st, part_a = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "부1"}
        )
        self.assertEqual(st, 201)
        st, part_b = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "부2"}
        )
        self.assertEqual(st, 201)

        folder_a = self._folder_id_for_part(pid, part_a["id"])
        folder_b = self._folder_id_for_part(pid, part_b["id"])

        st, result = self.request(
            "POST",
            f"/api/folders/{folder_b}/reparent",
            {
                "new_parent_id": folder_a,
                "position": "inside",
                "target_id": folder_a,
            },
        )
        self.assertEqual(st, 200, msg=result)
        self.assertTrue(result.get("moved"))
        self.assertEqual(result.get("parent_id"), folder_a)
        self.assertFalse(result.get("legacy_compatible"))
        self.assertFalse(result.get("folder_sync_complete"))
        self.assertGreaterEqual(int(result.get("max_depth") or 0), 2)

        row_b = self._folder_row(folder_b)
        self.assertEqual(int(row_b["parent_id"]), folder_a)

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            self.assertFalse(
                folder_tree.project_folder_tree_is_legacy_compatible(conn, pid)
            )
            self.assertFalse(folder_tree.project_folder_sync_complete(conn, pid))
            # Mapping rows still exist (parts still mapped)
            self.assertTrue(folder_tree.project_folder_mapping_complete(conn, pid))
            self.assertEqual(folder_tree.max_folder_depth(conn, pid), 2)

        # Outline must not fall back to legacy (would show both as root parts)
        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        # Folder path still serializes parts by source_kind=part at root only
        # (full nested `folders` field is 2-b). Parent_id update is what we assert here.

    def test_reparent_depth_three(self) -> None:
        st, project = self.request(
            "POST", "/api/projects", {"title": "깊이3", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        folders = []
        for title in ("부1", "부2", "부3"):
            st, part = self.request(
                "POST", f"/api/projects/{pid}/parts", {"title": title}
            )
            self.assertEqual(st, 201)
            folders.append(self._folder_id_for_part(pid, part["id"]))

        f1, f2, f3 = folders
        st, r1 = self.request(
            "POST",
            f"/api/folders/{f2}/reparent",
            {"new_parent_id": f1, "position": "inside", "target_id": f1},
        )
        self.assertEqual(st, 200, msg=r1)
        st, r2 = self.request(
            "POST",
            f"/api/folders/{f3}/reparent",
            {"new_parent_id": f2, "position": "inside", "target_id": f2},
        )
        self.assertEqual(st, 200, msg=r2)
        self.assertEqual(int(r2.get("max_depth") or 0), 3)
        self.assertFalse(r2.get("legacy_compatible"))

        self.assertEqual(int(self._folder_row(f2)["parent_id"]), f1)
        self.assertEqual(int(self._folder_row(f3)["parent_id"]), f2)

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            self.assertEqual(folder_tree.max_folder_depth(conn, pid), 3)
            # depth > 2 forces folder outline path
            handler = object.__new__(app.SuperToryHandler)
            outline = handler.project_outline(pid)
            self.assertIn("parts", outline)

    def test_reparent_rejects_cycle(self) -> None:
        st, project = self.request(
            "POST", "/api/projects", {"title": "순환", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        st, part_a = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "A"}
        )
        st, part_b = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "B"}
        )
        self.assertEqual(st, 201)
        fa = self._folder_id_for_part(pid, part_a["id"])
        fb = self._folder_id_for_part(pid, part_b["id"])

        st, _ = self.request(
            "POST",
            f"/api/folders/{fb}/reparent",
            {"new_parent_id": fa, "position": "inside", "target_id": fa},
        )
        self.assertEqual(st, 200)

        # B is under A; putting A under B is a cycle
        st, err = self.request(
            "POST",
            f"/api/folders/{fa}/reparent",
            {"new_parent_id": fb, "position": "inside", "target_id": fb},
        )
        self.assertEqual(st, 400)
        self.assertIn("error", err)
        self.assertRegex(str(err["error"]), r"순환|자손|자신")

        # Self as parent
        st, err2 = self.request(
            "POST",
            f"/api/folders/{fa}/reparent",
            {"new_parent_id": fa, "position": "inside", "target_id": fa},
        )
        self.assertEqual(st, 400)
        self.assertIn("error", err2)

    def test_reparent_before_after_sibling_order(self) -> None:
        st, project = self.request(
            "POST", "/api/projects", {"title": "순서", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        ids = []
        for title in ("A", "B", "C"):
            st, part = self.request(
                "POST", f"/api/projects/{pid}/parts", {"title": title}
            )
            self.assertEqual(st, 201)
            ids.append(self._folder_id_for_part(pid, part["id"]))
        fa, fb, fc = ids

        # Move C before A at root
        st, result = self.request(
            "POST",
            f"/api/folders/{fc}/reparent",
            {"new_parent_id": None, "position": "before", "target_id": fa},
        )
        self.assertEqual(st, 200, msg=result)
        self.assertTrue(result.get("moved"))
        self.assertIsNone(result.get("parent_id"))
        # Still legacy-compatible (all parts root)
        self.assertTrue(result.get("legacy_compatible"))
        self.assertTrue(result.get("folder_sync_complete"))

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            order = [
                int(r["id"])
                for r in conn.execute(
                    """
                    SELECT id FROM folder
                    WHERE project_id = ? AND parent_id IS NULL AND deleted_at IS NULL
                    ORDER BY sort_order, id
                    """,
                    (pid,),
                ).fetchall()
            ]
        self.assertEqual(order[0], fc)
        self.assertIn(fa, order)
        self.assertIn(fb, order)

        # Move C after B
        st, result2 = self.request(
            "POST",
            f"/api/folders/{fc}/reparent",
            {"new_parent_id": None, "position": "after", "target_id": fb},
        )
        self.assertEqual(st, 200, msg=result2)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            order2 = [
                int(r["id"])
                for r in conn.execute(
                    """
                    SELECT id FROM folder
                    WHERE project_id = ? AND parent_id IS NULL AND deleted_at IS NULL
                    ORDER BY sort_order, id
                    """,
                    (pid,),
                ).fetchall()
            ]
        self.assertEqual(order2.index(fc), order2.index(fb) + 1)

    def test_shallow_projects_still_sync_complete(self) -> None:
        """Dual-write shallow tree remains complete (legacy fallback policy unchanged)."""
        st, project = self.request(
            "POST", "/api/projects", {"title": "얕은", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "1권"}
        )
        self.assertEqual(st, 201)
        st, ch = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "1장", "part_id": part["id"]},
        )
        self.assertEqual(st, 201)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            self.assertTrue(folder_tree.project_folder_sync_complete(conn, pid))
            self.assertTrue(
                folder_tree.project_folder_tree_is_legacy_compatible(conn, pid)
            )
            self.assertLessEqual(folder_tree.max_folder_depth(conn, pid), 2)


if __name__ == "__main__":
    unittest.main()
