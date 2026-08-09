# -*- coding: utf-8 -*-
"""U3: folder.create / folder.trash undo."""
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


class FolderUndoU3Tests(unittest.TestCase):
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

    def _project(self) -> int:
        st, p = self.request(
            "POST", "/api/projects", {"title": "UndoU3", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        return int(p["id"])

    def _part(self, pid: int, title: str) -> tuple[int, int]:
        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": title}
        )
        self.assertEqual(st, 201)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fid = folder_tree.folder_id_for_source(
                conn, pid, "part", int(part["id"])
            )
        self.assertIsNotNone(fid)
        return int(part["id"]), int(fid)

    def _chapter(
        self, pid: int, title: str, part_id: int | None = None
    ) -> tuple[int, int]:
        body: dict = {"title": title}
        if part_id is not None:
            body["part_id"] = part_id
        st, ch = self.request("POST", f"/api/projects/{pid}/chapters", body)
        self.assertEqual(st, 201)
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            fid = folder_tree.folder_id_for_source(
                conn, pid, "chapter", int(ch["id"])
            )
        self.assertIsNotNone(fid)
        return int(ch["id"]), int(fid)

    def _scene(self, chapter_id: int, title: str) -> int:
        st, sc = self.request(
            "POST", f"/api/chapters/{chapter_id}/scenes", {"title": title}
        )
        self.assertEqual(st, 201)
        return int(sc["id"])

    def _deleted_at(self, table: str, row_id: int):
        with app.database() as conn:
            row = conn.execute(
                f"SELECT deleted_at FROM {table} WHERE id = ?", (row_id,)
            ).fetchone()
            self.assertIsNotNone(row)
            return row[0]

    def test_1_create_then_undo_soft_deletes(self) -> None:
        """새 폴더 생성(자식 없음) → undo → soft-delete."""
        pid = self._project()
        part_id, fid = self._part(pid, "부1")
        self.assertIsNone(self._deleted_at("folder", fid))
        self.assertIsNone(self._deleted_at("part", part_id))

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        self.assertTrue(undo.get("ok"))
        self.assertEqual(undo.get("type"), "folder.create")
        self.assertIsNotNone(self._deleted_at("folder", fid))
        self.assertIsNotNone(self._deleted_at("part", part_id))
        with app.database() as conn:
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 0)

    def test_2_create_with_child_blocks_undo_keeps_stack(self) -> None:
        """생성 후 자식 추가 → create undo 거부 + 스택 유지."""
        pid = self._project()
        part_id, fid = self._part(pid, "부1")
        # add child chapter under part
        self._chapter(pid, "1장", part_id=part_id)

        # stack top is chapter create, not part create — undo chapter first conceptually.
        # For this scenario: create empty part, then create chapter under it;
        # top is chapter create. Undo chapter (no children) succeeds.
        # Re-do: create part only is under stack if we undo chapter then try part.
        # Spec: create folder then add child then undo *that create*.
        # So: create chapter alone, add scene as child, undo chapter create → blocked.
        pid2 = self._project()
        ch_id, ch_fid = self._chapter(pid2, "빈장")
        self._scene(ch_id, "회차1")

        st, err = self.request("POST", f"/api/projects/{pid2}/undo", {})
        self.assertEqual(st, 400, msg=err)
        self.assertIn("error", err)
        self.assertIn("하위 항목", str(err.get("error") or err))
        # still active (not soft-deleted)
        self.assertIsNone(self._deleted_at("folder", ch_fid))
        self.assertIsNone(self._deleted_at("chapter", ch_id))
        with app.database() as conn:
            # stack kept
            self.assertGreaterEqual(
                folder_tree.count_active_action_logs(conn, pid2), 1
            )

    def test_3_trash_chapter_with_scenes_then_undo(self) -> None:
        """폴더 삭제(scene 포함) → undo → 전부 복원 + 위치."""
        pid = self._project()
        part_id, part_fid = self._part(pid, "부1")
        ch_id, ch_fid = self._chapter(pid, "1장", part_id=part_id)
        s1 = self._scene(ch_id, "회차1")
        s2 = self._scene(ch_id, "회차2")

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            before_parent = conn.execute(
                "SELECT parent_id, sort_order FROM folder WHERE id = ?",
                (ch_fid,),
            ).fetchone()
            parent_before = before_parent["parent_id"]
            sort_before = int(before_parent["sort_order"] or 0)

        st, tr = self.request("POST", f"/api/chapters/{ch_id}/trash", {})
        self.assertEqual(st, 200, msg=tr)
        self.assertIsNotNone(self._deleted_at("folder", ch_fid))
        self.assertIsNotNone(self._deleted_at("chapter", ch_id))
        self.assertIsNotNone(self._deleted_at("scene", s1))
        self.assertIsNotNone(self._deleted_at("scene", s2))

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        self.assertEqual(undo.get("type"), "folder.trash")
        self.assertIsNone(self._deleted_at("folder", ch_fid))
        self.assertIsNone(self._deleted_at("chapter", ch_id))
        self.assertIsNone(self._deleted_at("scene", s1))
        self.assertIsNone(self._deleted_at("scene", s2))

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            after = conn.execute(
                "SELECT parent_id, sort_order FROM folder WHERE id = ?",
                (ch_fid,),
            ).fetchone()
            self.assertEqual(after["parent_id"], parent_before)
            self.assertEqual(int(after["sort_order"] or 0), sort_before)
            # part still active
            self.assertIsNone(
                conn.execute(
                    "SELECT deleted_at FROM folder WHERE id = ?", (part_fid,)
                ).fetchone()["deleted_at"]
            )

    def test_4_nested_three_levels_trash_part_undo(self) -> None:
        """부1>부2>부3 통째 삭제 → undo → 트리 구조 복원."""
        pid = self._project()
        _, f1 = self._part(pid, "부1")
        _, f2 = self._part(pid, "부2")
        p3, f3 = self._part(pid, "부3")

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

        # Trash the outer part (부1) — cascade via part mapping + folder subtree
        # f1 is source part 부1
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            p1 = conn.execute(
                "SELECT source_id FROM folder WHERE id = ?", (f1,)
            ).fetchone()["source_id"]

        st, tr = self.request("POST", f"/api/parts/{int(p1)}/trash", {})
        self.assertEqual(st, 200, msg=tr)
        self.assertIsNotNone(self._deleted_at("folder", f1))
        # Nested folder rows soft-deleted with cascade_children
        self.assertIsNotNone(self._deleted_at("folder", f2))
        self.assertIsNotNone(self._deleted_at("folder", f3))

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            for fid in (f1, f2, f3):
                row = conn.execute(
                    "SELECT deleted_at, parent_id FROM folder WHERE id = ?",
                    (fid,),
                ).fetchone()
                self.assertIsNone(row["deleted_at"], msg=fid)
            self.assertIsNone(
                conn.execute(
                    "SELECT parent_id FROM folder WHERE id = ?", (f1,)
                ).fetchone()["parent_id"]
            )
            self.assertEqual(
                int(
                    conn.execute(
                        "SELECT parent_id FROM folder WHERE id = ?", (f2,)
                    ).fetchone()["parent_id"]
                ),
                f1,
            )
            self.assertEqual(
                int(
                    conn.execute(
                        "SELECT parent_id FROM folder WHERE id = ?", (f3,)
                    ).fetchone()["parent_id"]
                ),
                f2,
            )
        _ = p3

    def test_5_trash_undo_trash_undo_repeat(self) -> None:
        """삭제→undo→삭제→undo 반복."""
        pid = self._project()
        part_id, fid = self._part(pid, "반복부")
        for _ in range(2):
            st, tr = self.request("POST", f"/api/parts/{part_id}/trash", {})
            self.assertEqual(st, 200, msg=tr)
            self.assertIsNotNone(self._deleted_at("folder", fid))
            st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
            self.assertEqual(st, 200, msg=undo)
            self.assertIsNone(self._deleted_at("folder", fid))
            self.assertIsNone(self._deleted_at("part", part_id))

    def test_6_create_already_deleted_is_noop(self) -> None:
        """create 로그 상태에서 이미 soft-delete면 undo no-op."""
        pid = self._project()
        part_id, fid = self._part(pid, "미리삭제")
        # Soft-delete without consuming create log via trash API would add trash log.
        # Instead raw soft-delete folder+part, leave create log active.
        with app.database() as conn:
            conn.execute(
                "UPDATE folder SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (fid,),
            )
            conn.execute(
                "UPDATE part SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (part_id,),
            )
            conn.commit()
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 1)

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        self.assertTrue(undo.get("noop"))
        with app.database() as conn:
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 0)

    def test_7_trash_undo_conflict_when_folder_purged(self) -> None:
        """trash 후 folder 행 제거 → undo 충돌 skip."""
        pid = self._project()
        part_id, fid = self._part(pid, "소멸")
        st, tr = self.request("POST", f"/api/parts/{part_id}/trash", {})
        self.assertEqual(st, 200, msg=tr)

        with app.database() as conn:
            # Break FK-safe: null out scene.folder_id refs if any, then delete folder rows
            try:
                conn.execute(
                    "UPDATE scene SET folder_id = NULL WHERE folder_id = ?",
                    (fid,),
                )
            except sqlite3.OperationalError:
                pass
            conn.execute("DELETE FROM folder WHERE id = ?", (fid,))
            conn.commit()
            trash_logs_before = conn.execute(
                """
                SELECT COUNT(*) FROM folder_action_log
                WHERE project_id = ? AND type = 'folder.trash' AND undone_at IS NULL
                """,
                (pid,),
            ).fetchone()[0]
            self.assertEqual(int(trash_logs_before), 1)

        st, err = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 400, msg=err)
        with app.database() as conn:
            # trash entry skipped (undone); older create may still be active
            trash_active = conn.execute(
                """
                SELECT COUNT(*) FROM folder_action_log
                WHERE project_id = ? AND type = 'folder.trash' AND undone_at IS NULL
                """,
                (pid,),
            ).fetchone()[0]
            self.assertEqual(int(trash_active), 0)

    def test_create_with_chapter_move_skips_create_log(self) -> None:
        """create_part + chapter_id 편입 시 folder.create 로그 없음 (정책 A)."""
        pid = self._project()
        ch_id, _ = self._chapter(pid, "떠돌이장")
        with app.database() as conn:
            creates_before = conn.execute(
                """
                SELECT COUNT(*) FROM folder_action_log
                WHERE project_id = ? AND type = 'folder.create' AND undone_at IS NULL
                """,
                (pid,),
            ).fetchone()[0]

        st, part = self.request(
            "POST",
            f"/api/projects/{pid}/parts",
            {"title": "편입부", "chapter_id": ch_id},
        )
        self.assertEqual(st, 201, msg=part)
        with app.database() as conn:
            creates_after = conn.execute(
                """
                SELECT COUNT(*) FROM folder_action_log
                WHERE project_id = ? AND type = 'folder.create' AND undone_at IS NULL
                """,
                (pid,),
            ).fetchone()[0]
            # only the earlier chapter create — no new create for the part
            self.assertEqual(int(creates_after), int(creates_before))


if __name__ == "__main__":
    unittest.main()
