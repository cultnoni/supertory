# -*- coding: utf-8 -*-
"""U2: folder.reparent undo (parent + sibling index restore)."""
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


class FolderUndoU2Tests(unittest.TestCase):
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

    def _create_project(self, title: str = "UndoU2") -> int:
        st, project = self.request(
            "POST", "/api/projects", {"title": title, "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        return int(project["id"])

    def _create_part(self, project_id: int, title: str) -> tuple[int, int]:
        st, part = self.request(
            "POST", f"/api/projects/{project_id}/parts", {"title": title}
        )
        self.assertEqual(st, 201)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fid = folder_tree.folder_id_for_source(
                conn, project_id, "part", int(part["id"])
            )
            # U3 logs folder.create — remove so U2 tests isolate reparent logs
            conn.execute(
                """
                DELETE FROM folder_action_log
                WHERE project_id = ? AND type = 'folder.create'
                """,
                (project_id,),
            )
            conn.commit()
        self.assertIsNotNone(fid)
        return int(part["id"]), int(fid)

    def _folder_parent_and_index(
        self, project_id: int, folder_id: int
    ) -> tuple[int | None, int]:
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT parent_id FROM folder WHERE id = ? AND deleted_at IS NULL",
                (folder_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            raw = row["parent_id"]
            parent = int(raw) if raw is not None else None
            idx = folder_tree.folder_sibling_index(
                conn, project_id, folder_id, parent
            )
            self.assertIsNotNone(idx)
            return parent, int(idx)

    def _sibling_ids(
        self, project_id: int, parent_id: int | None
    ) -> list[int]:
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            return folder_tree.list_folder_sibling_ids(conn, project_id, parent_id)

    # --- scenarios ---

    def test_1_reparent_into_parent_then_undo_restores_root_order(self) -> None:
        """부2를 부1 안으로 reparent → undo → 원래 루트 형제 순서 복귀."""
        pid = self._create_project("nest")
        _, f1 = self._create_part(pid, "부1")
        _, f2 = self._create_part(pid, "부2")
        before = self._sibling_ids(pid, None)
        self.assertEqual(before, [f1, f2])

        st, r = self.request(
            "POST",
            f"/api/folders/{f2}/reparent",
            {
                "new_parent_id": f1,
                "position": "inside",
                "target_id": f1,
            },
        )
        self.assertEqual(st, 200, msg=r)
        self.assertTrue(r.get("moved"))
        parent, _ = self._folder_parent_and_index(pid, f2)
        self.assertEqual(parent, f1)

        with app.database() as conn:
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 1)

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        self.assertTrue(undo.get("ok"))
        self.assertEqual(undo.get("type"), "folder.reparent")
        self.assertFalse(undo.get("noop"))

        parent, idx = self._folder_parent_and_index(pid, f2)
        self.assertIsNone(parent)
        self.assertEqual(self._sibling_ids(pid, None), [f1, f2])
        self.assertEqual(idx, 1)
        with app.database() as conn:
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 0)

    def test_2_promote_from_depth3_then_undo(self) -> None:
        """부1>부2>부3 에서 부3 promote → undo → 다시 부2 아래."""
        pid = self._create_project("depth3")
        _, f1 = self._create_part(pid, "부1")
        _, f2 = self._create_part(pid, "부2")
        _, f3 = self._create_part(pid, "부3")

        st, r = self.request(
            "POST",
            f"/api/folders/{f2}/reparent",
            {"new_parent_id": f1, "position": "inside", "target_id": f1},
        )
        self.assertEqual(st, 200, msg=r)
        st, r = self.request(
            "POST",
            f"/api/folders/{f3}/reparent",
            {"new_parent_id": f2, "position": "inside", "target_id": f2},
        )
        self.assertEqual(st, 200, msg=r)
        parent, _ = self._folder_parent_and_index(pid, f3)
        self.assertEqual(parent, f2)

        # promote 부3 to root
        st, r = self.request(
            "POST",
            f"/api/folders/{f3}/reparent",
            {"new_parent_id": None, "position": "inside"},
        )
        self.assertEqual(st, 200, msg=r)
        self.assertTrue(r.get("moved"))
        parent, _ = self._folder_parent_and_index(pid, f3)
        self.assertIsNone(parent)

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        parent, idx = self._folder_parent_and_index(pid, f3)
        self.assertEqual(parent, f2)
        self.assertEqual(idx, 0)
        self.assertEqual(self._sibling_ids(pid, f2), [f3])

    def test_3_conflict_when_moved_again_without_matching_forward(self) -> None:
        """reparent 후 다른 곳으로 또 이동(raw) → undo 충돌 skip."""
        pid = self._create_project("conflict")
        _, f1 = self._create_part(pid, "부1")
        _, f2 = self._create_part(pid, "부2")
        _, f3 = self._create_part(pid, "부3")

        st, r = self.request(
            "POST",
            f"/api/folders/{f2}/reparent",
            {"new_parent_id": f1, "position": "inside", "target_id": f1},
        )
        self.assertEqual(st, 200, msg=r)

        # Silently move f2 under f3 (no action log) → forward state broken
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            folder_tree.reparent_folder(
                conn,
                f2,
                new_parent_id=f3,
                position="inside",
                new_parent_id_provided=True,
            )
            conn.commit()
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 1)

        st, err = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 400, msg=err)
        self.assertIn("error", err)

        parent, _ = self._folder_parent_and_index(pid, f2)
        self.assertEqual(parent, f3)  # third place kept
        with app.database() as conn:
            # entry skipped (undone_at set)
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 0)

    def test_4_sibling_index_among_five(self) -> None:
        """형제 5개 중 3번째를 다른 부모로 옮긴 뒤 undo → 다시 3번째."""
        pid = self._create_project("five")
        folders = []
        for i in range(1, 6):
            _, fid = self._create_part(pid, f"부{i}")
            folders.append(fid)
        f1, f2, f3, f4, f5 = folders
        self.assertEqual(self._sibling_ids(pid, None), [f1, f2, f3, f4, f5])

        # nest f5 under f1 first as destination parent with room
        st, r = self.request(
            "POST",
            f"/api/folders/{f3}/reparent",
            {"new_parent_id": f1, "position": "inside", "target_id": f1},
        )
        self.assertEqual(st, 200, msg=r)
        parent, _ = self._folder_parent_and_index(pid, f3)
        self.assertEqual(parent, f1)
        # remaining roots: f1, f2, f4, f5 — f3 was index 2 among original five
        self.assertEqual(self._sibling_ids(pid, None), [f1, f2, f4, f5])

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        parent, idx = self._folder_parent_and_index(pid, f3)
        self.assertIsNone(parent)
        self.assertEqual(idx, 2)
        self.assertEqual(self._sibling_ids(pid, None), [f1, f2, f3, f4, f5])

    def test_5_same_parent_reorder_undo(self) -> None:
        """같은 부모 내 before/after 순서 변경 → undo → 원래 index."""
        pid = self._create_project("reorder")
        folders = []
        for title in ("A", "B", "C", "D"):
            _, fid = self._create_part(pid, title)
            folders.append(fid)
        fa, fb, fc, fd = folders
        self.assertEqual(self._sibling_ids(pid, None), [fa, fb, fc, fd])

        # Move C before A (index 2 → 0)
        st, r = self.request(
            "POST",
            f"/api/folders/{fc}/reparent",
            {
                "new_parent_id": None,
                "position": "before",
                "target_id": fa,
            },
        )
        self.assertEqual(st, 200, msg=r)
        self.assertTrue(r.get("moved"))
        self.assertEqual(self._sibling_ids(pid, None), [fc, fa, fb, fd])

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        self.assertEqual(self._sibling_ids(pid, None), [fa, fb, fc, fd])
        parent, idx = self._folder_parent_and_index(pid, fc)
        self.assertIsNone(parent)
        self.assertEqual(idx, 2)

    def test_reparent_logs_folder_reparent_type(self) -> None:
        pid = self._create_project("logtype")
        _, f1 = self._create_part(pid, "부1")
        _, f2 = self._create_part(pid, "부2")
        st, r = self.request(
            "POST",
            f"/api/folders/{f2}/reparent",
            {"new_parent_id": f1, "position": "inside", "target_id": f1},
        )
        self.assertEqual(st, 200, msg=r)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT type, label_ko, payload_json FROM folder_action_log
                WHERE project_id = ? AND undone_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (pid,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["type"], "folder.reparent")
            payload = json.loads(row["payload_json"])
            self.assertEqual(payload["reverse"]["op"], "reparent")
            self.assertIn("old_sibling_ids", payload["forward"])
            self.assertEqual(payload["forward"]["new_parent_id"], f1)

    def test_undo_reparent_does_not_stack_extra_log(self) -> None:
        """undo 경로의 reparent_folder 호출이 추가 로그를 남기지 않음."""
        pid = self._create_project("nolog")
        _, f1 = self._create_part(pid, "부1")
        _, f2 = self._create_part(pid, "부2")
        st, _ = self.request(
            "POST",
            f"/api/folders/{f2}/reparent",
            {"new_parent_id": f1, "position": "inside", "target_id": f1},
        )
        self.assertEqual(st, 200)
        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        with app.database() as conn:
            reparent_total = conn.execute(
                """
                SELECT COUNT(*) AS c FROM folder_action_log
                WHERE project_id = ? AND type = 'folder.reparent'
                """,
                (pid,),
            ).fetchone()[0]
            active = folder_tree.count_active_action_logs(conn, pid)
            # one reparent log (marked undone); undo path must not append another
            self.assertEqual(int(reparent_total), 1)
            self.assertEqual(active, 0)


if __name__ == "__main__":
    unittest.main()
