# -*- coding: utf-8 -*-
"""Phase 029: folder color + is_pinned API and sibling sort."""
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


class FolderColorPinTests(unittest.TestCase):
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

    def test_migration_adds_color_and_is_pinned(self) -> None:
        with app.database() as conn:
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(folder)").fetchall()
            }
            self.assertIn("color", cols)
            self.assertIn("is_pinned", cols)
            ver = conn.execute(
                "SELECT 1 FROM schema_migration WHERE version = 29"
            ).fetchone()
            self.assertIsNotNone(ver)

    def test_color_round_trip(self) -> None:
        st, project = self.request(
            "POST", "/api/projects", {"title": "색상", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "1권"}
        )
        self.assertEqual(st, 201)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fid = folder_tree.folder_id_for_source(
                conn, pid, "part", int(part["id"])
            )
        self.assertIsNotNone(fid)

        st, saved = self.request(
            "PUT", f"/api/folders/{fid}", {"color": "green"}
        )
        self.assertEqual(st, 200, msg=saved)
        self.assertEqual(saved.get("color"), "green")
        self.assertEqual(int(saved.get("is_pinned") or 0), 0)

        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        roots = outline.get("folders") or []
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].get("color"), "green")
        self.assertEqual(int(roots[0].get("is_pinned") or 0), 0)

        # clear color
        st, cleared = self.request(
            "PUT", f"/api/folders/{fid}", {"color": None}
        )
        self.assertEqual(st, 200, msg=cleared)
        self.assertIsNone(cleared.get("color"))

        st, bad = self.request(
            "PUT", f"/api/folders/{fid}", {"color": "neon"}
        )
        self.assertEqual(st, 400)

    def test_is_pinned_sibling_sort(self) -> None:
        st, project = self.request(
            "POST", "/api/projects", {"title": "고정", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        titles = []
        folder_ids = []
        for title in ("A", "B", "C"):
            st, part = self.request(
                "POST", f"/api/projects/{pid}/parts", {"title": title}
            )
            self.assertEqual(st, 201)
            titles.append(title)
            with app.database() as conn:
                conn.row_factory = sqlite3.Row
                folder_ids.append(
                    folder_tree.folder_id_for_source(
                        conn, pid, "part", int(part["id"])
                    )
                )

        # Pin B (middle by sort_order)
        fb = folder_ids[1]
        st, pinned = self.request(
            "PUT", f"/api/folders/{fb}", {"is_pinned": True}
        )
        self.assertEqual(st, 200, msg=pinned)
        self.assertEqual(int(pinned.get("is_pinned") or 0), 1)

        # sort_order of B must be unchanged
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT sort_order, is_pinned FROM folder WHERE id = ?",
                (fb,),
            ).fetchone()
            self.assertEqual(int(row["is_pinned"]), 1)
            self.assertEqual(int(row["sort_order"]), 1)

        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        roots = outline.get("folders") or []
        self.assertEqual([r["title"] for r in roots], ["B", "A", "C"])
        self.assertEqual(int(roots[0].get("is_pinned") or 0), 1)

        # Unpin → back to sort_order order A,B,C
        st, unpinned = self.request(
            "PUT", f"/api/folders/{fb}", {"is_pinned": False}
        )
        self.assertEqual(st, 200, msg=unpinned)
        st, outline2 = self.request("GET", f"/api/projects/{pid}/outline")
        roots2 = outline2.get("folders") or []
        self.assertEqual([r["title"] for r in roots2], ["A", "B", "C"])

    def test_is_box_toggle_independent(self) -> None:
        st, project = self.request(
            "POST", "/api/projects", {"title": "박스", "main_genre": "판타지"}
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
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fa = folder_tree.folder_id_for_source(conn, pid, "part", int(part_a["id"]))
            fb = folder_tree.folder_id_for_source(conn, pid, "part", int(part_b["id"]))
        # Nest B under A
        st, _ = self.request(
            "POST",
            f"/api/folders/{fb}/reparent",
            {"new_parent_id": fa, "position": "inside", "target_id": fa},
        )
        self.assertEqual(st, 200)
        # Box both
        st, ra = self.request("PUT", f"/api/folders/{fa}", {"is_box": True})
        st, rb = self.request("PUT", f"/api/folders/{fb}", {"is_box": True})
        self.assertEqual(st, 200, msg=rb)
        self.assertEqual(int(ra.get("is_box") or 0), 1)
        self.assertEqual(int(rb.get("is_box") or 0), 1)
        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        roots = outline.get("folders") or []
        self.assertEqual(len(roots), 1)
        self.assertTrue(roots[0].get("is_box"))
        kids = roots[0].get("children") or []
        self.assertEqual(len(kids), 1)
        self.assertTrue(kids[0].get("is_box"))
        # Unbox parent only — child stays boxed
        st, ra2 = self.request("PUT", f"/api/folders/{fa}", {"is_box": False})
        self.assertEqual(int(ra2.get("is_box") or 0), 0)
        st, outline2 = self.request("GET", f"/api/projects/{pid}/outline")
        roots2 = outline2.get("folders") or []
        self.assertFalse(bool(roots2[0].get("is_box")))
        self.assertTrue(bool((roots2[0].get("children") or [{}])[0].get("is_box")))

    def test_default_color_pin_on_new_folders(self) -> None:
        st, project = self.request(
            "POST", "/api/projects", {"title": "기본값", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "권"}
        )
        self.assertEqual(st, 201)
        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        root = (outline.get("folders") or [None])[0]
        self.assertIsNotNone(root)
        self.assertIn(root.get("color"), (None, ""))
        self.assertEqual(int(root.get("is_pinned") or 0), 0)


if __name__ == "__main__":
    unittest.main()
