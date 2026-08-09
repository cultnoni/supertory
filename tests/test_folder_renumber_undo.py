# -*- coding: utf-8 -*-
"""folder.renumber_titles bulk undo/redo."""
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


class FolderRenumberUndoTests(unittest.TestCase):
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

    def _seed_five_episodes(self) -> tuple[int, list[int], list[str]]:
        st, project = self.request(
            "POST", "/api/projects", {"title": "Renumber", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        originals = [f"{n}화 에피소드" for n in range(5, 10)]
        chapter_ids = []
        for title in originals:
            st, ch = self.request(
                "POST", f"/api/projects/{pid}/chapters", {"title": title}
            )
            self.assertEqual(st, 201)
            chapter_ids.append(int(ch["id"]))
        with app.database() as conn:
            # drop create logs so renumber is the only active stack entry after renumber
            conn.execute(
                "DELETE FROM folder_action_log WHERE project_id = ?",
                (pid,),
            )
            conn.commit()
        return pid, chapter_ids, originals

    def _titles(self, chapter_ids: list[int]) -> list[str]:
        with app.database() as conn:
            out = []
            for cid in chapter_ids:
                row = conn.execute(
                    "SELECT title FROM chapter WHERE id = ?", (cid,)
                ).fetchone()
                out.append(str(row[0] if row else ""))
            return out

    def _folder_titles(self, pid: int, chapter_ids: list[int]) -> list[str]:
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            out = []
            for cid in chapter_ids:
                fid = folder_tree.folder_id_for_source(conn, pid, "chapter", cid)
                if fid is None:
                    out.append("")
                    continue
                row = conn.execute(
                    "SELECT title FROM folder WHERE id = ?", (fid,)
                ).fetchone()
                out.append(str(row["title"] if row else ""))
            return out

    def test_1_renumber_undo_restores_all_titles(self) -> None:
        pid, chapter_ids, originals = self._seed_five_episodes()
        st, result = self.request(
            "PUT",
            f"/api/projects/{pid}/chapters/renumber-titles",
            {"style": "jang"},
        )
        self.assertEqual(st, 200, msg=result)
        self.assertGreater(int(result.get("changed") or 0), 0)
        after = self._titles(chapter_ids)
        self.assertNotEqual(after, originals)
        self.assertTrue(after[0].startswith("1장"))

        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        self.assertEqual(undo.get("type"), "folder.renumber_titles")
        self.assertEqual(self._titles(chapter_ids), originals)
        self.assertEqual(self._folder_titles(pid, chapter_ids), originals)

    def test_2_undo_then_redo_reapplies(self) -> None:
        pid, chapter_ids, originals = self._seed_five_episodes()
        st, result = self.request(
            "PUT",
            f"/api/projects/{pid}/chapters/renumber-titles",
            {"style": "jang"},
        )
        self.assertEqual(st, 200, msg=result)
        renumbered = self._titles(chapter_ids)

        st, _ = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200)
        self.assertEqual(self._titles(chapter_ids), originals)

        st, redo = self.request("POST", f"/api/projects/{pid}/redo", {})
        self.assertEqual(st, 200, msg=redo)
        self.assertEqual(self._titles(chapter_ids), renumbered)
        with app.database() as conn:
            self.assertEqual(folder_tree.count_undone_action_logs(conn, pid), 0)
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 1)

    def test_3_new_action_clears_redo(self) -> None:
        pid, chapter_ids, _ = self._seed_five_episodes()
        st, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/chapters/renumber-titles",
            {"style": "jang"},
        )
        self.assertEqual(st, 200)
        st, _ = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200)
        with app.database() as conn:
            self.assertEqual(folder_tree.count_undone_action_logs(conn, pid), 1)

        # another action invalidates redo
        st, _ = self.request(
            "PUT", f"/api/chapters/{chapter_ids[0]}", {"title": "수동 제목"}
        )
        self.assertEqual(st, 200)
        with app.database() as conn:
            self.assertEqual(folder_tree.count_undone_action_logs(conn, pid), 0)
        st, err = self.request("POST", f"/api/projects/{pid}/redo", {})
        self.assertEqual(st, 400, msg=err)

    def test_4_no_change_skips_log(self) -> None:
        pid, chapter_ids, _ = self._seed_five_episodes()
        # First renumber creates log
        st, r1 = self.request(
            "PUT",
            f"/api/projects/{pid}/chapters/renumber-titles",
            {"style": "jang"},
        )
        self.assertEqual(st, 200)
        self.assertGreater(int(r1.get("changed") or 0), 0)
        with app.database() as conn:
            n1 = folder_tree.count_active_action_logs(conn, pid)
            self.assertEqual(n1, 1)

        # Second renumber with same style should change 0
        st, r2 = self.request(
            "PUT",
            f"/api/projects/{pid}/chapters/renumber-titles",
            {"style": "jang"},
        )
        self.assertEqual(st, 200, msg=r2)
        self.assertEqual(int(r2.get("changed") or 0), 0)
        with app.database() as conn:
            # no extra active log (invalidate would clear redo only; no new insert)
            self.assertEqual(folder_tree.count_active_action_logs(conn, pid), 1)
            top = folder_tree.fetch_undo_stack_top(conn, pid)
            self.assertEqual(top["type"] if hasattr(top, "keys") else top[3], "folder.renumber_titles")

    def test_5_regression_other_undo_still_works(self) -> None:
        """Smoke: rename undo still works alongside renumber type registration."""
        pid, chapter_ids, _ = self._seed_five_episodes()
        st, _ = self.request(
            "PUT", f"/api/chapters/{chapter_ids[0]}", {"title": "임시이름"}
        )
        self.assertEqual(st, 200)
        st, undo = self.request("POST", f"/api/projects/{pid}/undo", {})
        self.assertEqual(st, 200, msg=undo)
        self.assertEqual(undo.get("type"), "folder.rename")
        self.assertEqual(self._titles(chapter_ids)[0], "5화 에피소드")


if __name__ == "__main__":
    unittest.main()
