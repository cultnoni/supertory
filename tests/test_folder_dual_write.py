"""Phase 3-3-a: dual-write keeps folder tree in sync after binder writes."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import folder_tree


class FolderDualWriteTests(unittest.TestCase):
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

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
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

    def assert_folder_complete(self, project_id: int) -> None:
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            self.assertTrue(
                folder_tree.project_folder_sync_complete(conn, int(project_id)),
                f"folder map incomplete for project {project_id}",
            )

    def assert_legacy_matches_folder(self, project_id: int) -> None:
        handler = object.__new__(app.SuperToryHandler)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            self.assertTrue(folder_tree.project_folder_sync_complete(conn, project_id))
            legacy = handler._project_outline_from_legacy(conn, project_id)
            folder = handler._project_outline_from_folder(conn, project_id)

        def titles(outline: dict) -> list:
            out = []
            for p in outline.get("parts") or []:
                out.append(("part", p["title"]))
                for c in p.get("chapters") or []:
                    out.append(("ch", c["title"], c.get("part_id")))
                    for s in c.get("scenes_flat") or []:
                        out.append(("sc", s["title"], s["id"]))
            for c in outline.get("ungrouped_chapters") or []:
                out.append(("ung", c["title"]))
                for s in c.get("scenes_flat") or []:
                    out.append(("sc", s["title"], s["id"]))
            return out

        self.assertEqual(titles(legacy), titles(folder))

    def test_create_rename_reorder_move_trash_keeps_folder_complete(self) -> None:
        st, project = self.request(
            "POST",
            "/api/projects",
            {"title": "듀얼라이트", "main_genre": "판타지"},
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        # Empty project may have zero parts/chapters — complete is True
        self.assert_folder_complete(pid)

        st, part_a = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "1권"}
        )
        self.assertEqual(st, 201)
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

        st, part_b = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "2권"}
        )
        self.assertEqual(st, 201)
        self.assert_folder_complete(pid)

        st, ch1 = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "1장", "part_id": part_a["id"]},
        )
        self.assertEqual(st, 201)
        self.assert_folder_complete(pid)

        st, ch2 = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "2장", "part_id": part_a["id"]},
        )
        self.assertEqual(st, 201)
        self.assert_folder_complete(pid)

        st, sc = self.request(
            "POST",
            f"/api/chapters/{ch1['id']}/scenes",
            {"title": "회차1"},
        )
        self.assertIn(st, (200, 201))
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

        # Rename chapter (3-3-b-1: folder-first dual-write pilot)
        st, _ = self.request(
            "PUT",
            f"/api/chapters/{ch1['id']}",
            {"title": "1장 개명"},
        )
        self.assertEqual(st, 200)
        self.assert_folder_complete(pid)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            leg = conn.execute(
                "SELECT title FROM chapter WHERE id = ?",
                (ch1["id"],),
            ).fetchone()
            fold = conn.execute(
                """
                SELECT title FROM folder
                WHERE source_kind = 'chapter' AND source_id = ? AND deleted_at IS NULL
                """,
                (ch1["id"],),
            ).fetchone()
            self.assertEqual(leg["title"], "1장 개명")
            self.assertIsNotNone(fold)
            self.assertEqual(fold["title"], "1장 개명")
        self.assert_legacy_matches_folder(pid)

        # Rename part (3-3-b-2: folder-first dual-write, same pattern as save_chapter)
        st, _ = self.request(
            "PUT",
            f"/api/parts/{part_a['id']}",
            {"title": "제1권"},
        )
        self.assertEqual(st, 200)
        self.assert_folder_complete(pid)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            leg = conn.execute(
                "SELECT title FROM part WHERE id = ?",
                (part_a["id"],),
            ).fetchone()
            fold = conn.execute(
                """
                SELECT title FROM folder
                WHERE source_kind = 'part' AND source_id = ? AND deleted_at IS NULL
                """,
                (part_a["id"],),
            ).fetchone()
            self.assertEqual(leg["title"], "제1권")
            self.assertIsNotNone(fold)
            self.assertEqual(fold["title"], "제1권")
        self.assert_legacy_matches_folder(pid)

        # Reorder chapters within part
        st, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/chapters/reorder",
            {"chapter_ids": [ch2["id"], ch1["id"]], "part_id": part_a["id"]},
        )
        self.assertEqual(st, 200)
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

        # Move chapter to other part
        st, moved = self.request(
            "POST",
            f"/api/chapters/{ch2['id']}/move",
            {"part_id": part_b["id"]},
        )
        self.assertEqual(st, 200)
        self.assertTrue(moved.get("moved"))
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

        # Reorder parts
        st, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/parts/reorder",
            {"part_ids": [part_b["id"], part_a["id"]]},
        )
        self.assertEqual(st, 200)
        self.assert_folder_complete(pid)

        # Trash scene (3-3-b-4) — no folder row; only scene soft-delete
        st, _ = self.request("POST", f"/api/scenes/{sc['id']}/trash", {})
        self.assertEqual(st, 200)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT deleted_at FROM scene WHERE id = ?", (sc["id"],)
            ).fetchone()
            self.assertIsNotNone(row["deleted_at"])
        self.assert_folder_complete(pid)

        # Trash chapter (with remaining scenes if any) — covered in dedicated test too
        st, _ = self.request("POST", f"/api/chapters/{ch1['id']}/trash", {})
        self.assertEqual(st, 200)
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

        # Trash part
        st, _ = self.request("POST", f"/api/parts/{part_b['id']}/trash", {})
        self.assertEqual(st, 200)
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

    def test_folder_first_create_chain_and_under_scene_chapter(self) -> None:
        """3-3-b-3: create_part → chapter → scene; under-scene chapter parent folder."""
        st, project = self.request(
            "POST",
            "/api/projects",
            {"title": "생성체인", "main_genre": "판타지"},
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])

        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "1권-생성"}
        )
        self.assertEqual(st, 201)
        self.assert_folder_complete(pid)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            pf = conn.execute(
                """
                SELECT id, parent_id, is_box, source_kind, source_id, title
                FROM folder
                WHERE source_kind = 'part' AND source_id = ? AND deleted_at IS NULL
                """,
                (part["id"],),
            ).fetchone()
            self.assertIsNotNone(pf)
            self.assertIsNone(pf["parent_id"])
            self.assertEqual(int(pf["is_box"]), 1)
            self.assertEqual(pf["title"], "1권-생성")
            self.assertEqual(int(pf["source_id"]), int(part["id"]))
            # Mapping is by source_id; folder.id may coincide with part.id on empty DBs

        st, ch = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "1장-생성", "part_id": part["id"]},
        )
        self.assertEqual(st, 201)
        self.assert_folder_complete(pid)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            part_folder = conn.execute(
                "SELECT id FROM folder WHERE source_kind='part' AND source_id=?",
                (part["id"],),
            ).fetchone()
            ch_folder = conn.execute(
                """
                SELECT id, parent_id, is_box, source_id, title
                FROM folder
                WHERE source_kind = 'chapter' AND source_id = ? AND deleted_at IS NULL
                """,
                (ch["id"],),
            ).fetchone()
            self.assertIsNotNone(ch_folder)
            self.assertEqual(int(ch_folder["parent_id"]), int(part_folder["id"]))
            self.assertEqual(int(ch_folder["is_box"]), 0)
            self.assertEqual(ch_folder["title"], "1장-생성")
            leg = conn.execute(
                "SELECT part_id FROM chapter WHERE id = ?", (ch["id"],)
            ).fetchone()
            self.assertEqual(int(leg["part_id"]), int(part["id"]))

        st, sc = self.request(
            "POST",
            f"/api/chapters/{ch['id']}/scenes",
            {"title": "회차-생성"},
        )
        self.assertIn(st, (200, 201))
        self.assert_folder_complete(pid)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            ch_folder = conn.execute(
                "SELECT id FROM folder WHERE source_kind='chapter' AND source_id=?",
                (ch["id"],),
            ).fetchone()
            scene = conn.execute(
                "SELECT chapter_id, folder_id, title FROM scene WHERE id = ?",
                (sc["id"],),
            ).fetchone()
            self.assertEqual(int(scene["chapter_id"]), int(ch["id"]))
            self.assertEqual(int(scene["folder_id"]), int(ch_folder["id"]))
            self.assertEqual(scene["title"], "회차-생성")
        self.assert_legacy_matches_folder(pid)

        # Under-scene chapter: parent folder = host scene's chapter folder
        st, under = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "원고아래폴더", "parent_scene_id": sc["id"]},
        )
        self.assertEqual(st, 201, under)
        self.assert_folder_complete(pid)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            host_folder = conn.execute(
                "SELECT id FROM folder WHERE source_kind='chapter' AND source_id=?",
                (ch["id"],),
            ).fetchone()
            under_folder = conn.execute(
                """
                SELECT parent_id, source_id, title, is_box
                FROM folder
                WHERE source_kind = 'chapter' AND source_id = ? AND deleted_at IS NULL
                """,
                (under["id"],),
            ).fetchone()
            self.assertIsNotNone(under_folder)
            self.assertEqual(int(under_folder["parent_id"]), int(host_folder["id"]))
            self.assertEqual(int(under_folder["is_box"]), 0)
            self.assertEqual(under_folder["title"], "원고아래폴더")
            leg = conn.execute(
                "SELECT parent_scene_id, part_id FROM chapter WHERE id = ?",
                (under["id"],),
            ).fetchone()
            self.assertEqual(int(leg["parent_scene_id"]), int(sc["id"]))
        self.assert_legacy_matches_folder(pid)

    def test_folder_first_trash_scene_chapter_part_cascade(self) -> None:
        """3-3-b-4: soft-delete cascade matches on folder + legacy."""
        st, project = self.request(
            "POST",
            "/api/projects",
            {"title": "삭제검증", "main_genre": "판타지"},
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])

        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "삭제권"}
        )
        self.assertEqual(st, 201)
        st, ch_a = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "삭제장A", "part_id": part["id"]},
        )
        self.assertEqual(st, 201)
        st, ch_b = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "삭제장B", "part_id": part["id"]},
        )
        self.assertEqual(st, 201)
        scene_ids = []
        for title, ch in (
            ("s1", ch_a),
            ("s2", ch_a),
            ("s3", ch_b),
        ):
            st, sc = self.request(
                "POST",
                f"/api/chapters/{ch['id']}/scenes",
                {"title": title},
            )
            self.assertIn(st, (200, 201))
            scene_ids.append(sc["id"])
        self.assert_folder_complete(pid)

        # --- scene only ---
        st, _ = self.request("POST", f"/api/scenes/{scene_ids[0]}/trash", {})
        self.assertEqual(st, 200)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            self.assertIsNotNone(
                conn.execute(
                    "SELECT deleted_at FROM scene WHERE id=?", (scene_ids[0],)
                ).fetchone()["deleted_at"]
            )
            # chapter folder still active after scene-only trash
            ch_f = conn.execute(
                "SELECT deleted_at FROM folder WHERE source_kind='chapter' AND source_id=?",
                (ch_a["id"],),
            ).fetchone()
            self.assertIsNone(ch_f["deleted_at"])
        self.assert_folder_complete(pid)

        # --- chapter with remaining scene ---
        st, tr = self.request("POST", f"/api/chapters/{ch_a['id']}/trash", {})
        self.assertEqual(st, 200)
        self.assertEqual(tr.get("scene_count"), 1)  # s2 left after s1 trashed
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            self.assertIsNotNone(
                conn.execute(
                    "SELECT deleted_at FROM chapter WHERE id=?", (ch_a["id"],)
                ).fetchone()["deleted_at"]
            )
            self.assertIsNotNone(
                conn.execute(
                    """
                    SELECT deleted_at FROM folder
                    WHERE source_kind='chapter' AND source_id=?
                    """,
                    (ch_a["id"],),
                ).fetchone()["deleted_at"]
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT deleted_at FROM scene WHERE id=?", (scene_ids[1],)
                ).fetchone()["deleted_at"]
            )
            # other chapter/scene still active
            self.assertIsNone(
                conn.execute(
                    "SELECT deleted_at FROM chapter WHERE id=?", (ch_b["id"],)
                ).fetchone()["deleted_at"]
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT deleted_at FROM scene WHERE id=?", (scene_ids[2],)
                ).fetchone()["deleted_at"]
            )
            self.assertIsNone(
                conn.execute(
                    """
                    SELECT deleted_at FROM folder
                    WHERE source_kind='chapter' AND source_id=?
                    """,
                    (ch_b["id"],),
                ).fetchone()["deleted_at"]
            )
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

        # --- part cascade: remaining chapter B + scene s3 ---
        st, trp = self.request("POST", f"/api/parts/{part['id']}/trash", {})
        self.assertEqual(st, 200)
        self.assertEqual(trp.get("chapter_count"), 1)
        self.assertEqual(trp.get("scene_count"), 1)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            self.assertIsNotNone(
                conn.execute(
                    "SELECT deleted_at FROM part WHERE id=?", (part["id"],)
                ).fetchone()["deleted_at"]
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT deleted_at FROM folder WHERE source_kind='part' AND source_id=?",
                    (part["id"],),
                ).fetchone()["deleted_at"]
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT deleted_at FROM chapter WHERE id=?", (ch_b["id"],)
                ).fetchone()["deleted_at"]
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT deleted_at FROM folder WHERE source_kind='chapter' AND source_id=?",
                    (ch_b["id"],),
                ).fetchone()["deleted_at"]
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT deleted_at FROM scene WHERE id=?", (scene_ids[2],)
                ).fetchone()["deleted_at"]
            )
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

    def test_folder_first_move_and_reorder(self) -> None:
        """3-3-b-5: move chapter/scene + reorder parts/chapters keep both trees aligned."""
        st, project = self.request(
            "POST",
            "/api/projects",
            {"title": "이동검증", "main_genre": "판타지"},
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])

        st, part_a = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "A권"}
        )
        st, part_b = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "B권"}
        )
        st, part_c = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "C권"}
        )
        self.assertEqual(st, 201)

        st, ch1 = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "장1", "part_id": part_a["id"]},
        )
        st, ch2 = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "장2", "part_id": part_a["id"]},
        )
        st, ch3 = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "장3", "part_id": part_b["id"]},
        )
        self.assertEqual(st, 201)

        st, sc1 = self.request(
            "POST", f"/api/chapters/{ch1['id']}/scenes", {"title": "씬1"}
        )
        st, sc2 = self.request(
            "POST", f"/api/chapters/{ch1['id']}/scenes", {"title": "씬2"}
        )
        self.assertIn(st, (200, 201))
        self.assert_folder_complete(pid)

        # reorder parts: C, A, B
        st, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/parts/reorder",
            {"part_ids": [part_c["id"], part_a["id"], part_b["id"]]},
        )
        self.assertEqual(st, 200)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            for src_id, expect_sort in (
                (part_c["id"], 0),
                (part_a["id"], 1),
                (part_b["id"], 2),
            ):
                leg = conn.execute(
                    "SELECT sort_order FROM part WHERE id=?", (src_id,)
                ).fetchone()
                fold = conn.execute(
                    """
                    SELECT sort_order FROM folder
                    WHERE source_kind='part' AND source_id=? AND deleted_at IS NULL
                    """,
                    (src_id,),
                ).fetchone()
                self.assertEqual(int(leg["sort_order"]), expect_sort)
                self.assertEqual(int(fold["sort_order"]), expect_sort)
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

        # reorder chapters in part A: ch2, ch1
        st, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/chapters/reorder",
            {"chapter_ids": [ch2["id"], ch1["id"]], "part_id": part_a["id"]},
        )
        self.assertEqual(st, 200)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            part_folder = conn.execute(
                "SELECT id FROM folder WHERE source_kind='part' AND source_id=?",
                (part_a["id"],),
            ).fetchone()
            for src_id, expect_sort in ((ch2["id"], 0), (ch1["id"], 1)):
                leg = conn.execute(
                    "SELECT sort_order, part_id FROM chapter WHERE id=?",
                    (src_id,),
                ).fetchone()
                fold = conn.execute(
                    """
                    SELECT sort_order, parent_id FROM folder
                    WHERE source_kind='chapter' AND source_id=? AND deleted_at IS NULL
                    """,
                    (src_id,),
                ).fetchone()
                self.assertEqual(int(leg["sort_order"]), expect_sort)
                self.assertEqual(int(leg["part_id"]), int(part_a["id"]))
                self.assertEqual(int(fold["sort_order"]), expect_sort)
                self.assertEqual(int(fold["parent_id"]), int(part_folder["id"]))
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

        # move chapter ch2 from A to B
        st, moved = self.request(
            "POST",
            f"/api/chapters/{ch2['id']}/move",
            {"part_id": part_b["id"]},
        )
        self.assertEqual(st, 200)
        self.assertTrue(moved.get("moved"))
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            b_folder = conn.execute(
                "SELECT id FROM folder WHERE source_kind='part' AND source_id=?",
                (part_b["id"],),
            ).fetchone()
            a_folder = conn.execute(
                "SELECT id FROM folder WHERE source_kind='part' AND source_id=?",
                (part_a["id"],),
            ).fetchone()
            ch2_f = conn.execute(
                """
                SELECT parent_id FROM folder
                WHERE source_kind='chapter' AND source_id=? AND deleted_at IS NULL
                """,
                (ch2["id"],),
            ).fetchone()
            self.assertEqual(int(ch2_f["parent_id"]), int(b_folder["id"]))
            leg = conn.execute(
                "SELECT part_id FROM chapter WHERE id=?", (ch2["id"],)
            ).fetchone()
            self.assertEqual(int(leg["part_id"]), int(part_b["id"]))
            # A should only have ch1 as active top-level chapter under part
            a_children = conn.execute(
                """
                SELECT source_id FROM folder
                WHERE parent_id=? AND source_kind='chapter' AND deleted_at IS NULL
                ORDER BY sort_order
                """,
                (a_folder["id"],),
            ).fetchall()
            self.assertEqual([int(r["source_id"]) for r in a_children], [int(ch1["id"])])
            b_children = conn.execute(
                """
                SELECT source_id FROM folder
                WHERE parent_id=? AND source_kind='chapter' AND deleted_at IS NULL
                ORDER BY sort_order
                """,
                (b_folder["id"],),
            ).fetchall()
            self.assertEqual(
                sorted(int(r["source_id"]) for r in b_children),
                sorted([int(ch2["id"]), int(ch3["id"])]),
            )
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)

        # move scene sc2 from ch1 to ch3
        st, sm = self.request(
            "POST",
            f"/api/scenes/{sc2['id']}/move",
            {"chapter_id": ch3["id"], "parent_scene_id": None},
        )
        self.assertEqual(st, 200, sm)
        self.assertTrue(sm.get("moved", True) or sm.get("chapter_id") == ch3["id"])
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            ch3_folder = conn.execute(
                "SELECT id FROM folder WHERE source_kind='chapter' AND source_id=?",
                (ch3["id"],),
            ).fetchone()
            sc = conn.execute(
                "SELECT chapter_id, folder_id FROM scene WHERE id=?",
                (sc2["id"],),
            ).fetchone()
            self.assertEqual(int(sc["chapter_id"]), int(ch3["id"]))
            self.assertEqual(int(sc["folder_id"]), int(ch3_folder["id"]))
        self.assert_folder_complete(pid)
        self.assert_legacy_matches_folder(pid)


if __name__ == "__main__":
    unittest.main()
