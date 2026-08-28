"""Phase 3-1/3-2: outline read via folder table, legacy JSON shape + import order."""
from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import folder_tree


class FolderOutlineReadTests(unittest.TestCase):
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
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(
            method,
            path,
            body,
            {"Content-Type": "application/json"} if body else {},
        )
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _seed_hierarchy(self) -> int:
        st, project = self.request(
            "POST",
            "/api/projects",
            {"title": "아웃라인 비교", "main_genre": "판타지"},
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        st, part = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "1권"}
        )
        self.assertEqual(st, 201)
        st, ch1 = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "1장", "part_id": part["id"]},
        )
        self.assertEqual(st, 201)
        st, ch2 = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "2장", "part_id": part["id"]},
        )
        self.assertEqual(st, 201)
        for title, ch in (("씬A", ch1), ("씬B", ch1), ("씬C", ch2)):
            st, sc = self.request(
                "POST",
                f"/api/chapters/{ch['id']}/scenes",
                {"title": title},
            )
            self.assertIn(st, (200, 201))
        return pid

    def test_folder_outline_matches_legacy_semantically(self) -> None:
        pid = self._seed_hierarchy()
        # Writes still go to part/chapter only — force folder sync like backfill/import
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            folder_tree.sync_project_folder_tree(conn, pid)
            self.assertTrue(folder_tree.project_folder_sync_complete(conn, pid))

        handler = object.__new__(app.SuperToryHandler)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            legacy = handler._project_outline_from_legacy(conn, pid)
            folder = handler._project_outline_from_folder(conn, pid)

        def strip_preview(node):
            if isinstance(node, dict):
                return {
                    k: strip_preview(v)
                    for k, v in node.items()
                    if k != "body_preview"
                }
            if isinstance(node, list):
                return [strip_preview(x) for x in node]
            return node

        # Structure: part titles, chapter titles under part, scene titles order
        self.assertEqual(
            [p["title"] for p in legacy["parts"]],
            [p["title"] for p in folder["parts"]],
        )
        for lp, fp in zip(legacy["parts"], folder["parts"]):
            self.assertEqual(lp["id"], fp["id"])
            self.assertEqual(
                [c["title"] for c in lp["chapters"]],
                [c["title"] for c in fp["chapters"]],
            )
            for lc, fc in zip(lp["chapters"], fp["chapters"]):
                self.assertEqual(lc["id"], fc["id"])
                self.assertEqual(lc["part_id"], fc["part_id"])
                self.assertEqual(
                    [s["title"] for s in lc.get("scenes_flat") or []],
                    [s["title"] for s in fc.get("scenes_flat") or []],
                )

        # Public GET uses folder path when complete
        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        self.assertEqual(outline["parts"][0]["title"], "1권")
        ch_titles = [c["title"] for c in outline["parts"][0]["chapters"]]
        self.assertEqual(ch_titles, ["1장", "2장"])
        scenes_1 = [
            s["title"]
            for s in outline["parts"][0]["chapters"][0]["scenes_flat"]
        ]
        self.assertEqual(scenes_1, ["씬A", "씬B"])

    def test_import_hierarchy_order_via_folder_outline(self) -> None:
        content = """
목차
1부
1장. 시작
2장. 중간
2부
3장. 끝

1부
1장. 시작
시작 본문입니다.

2장. 중간
중간 본문입니다.

2부
3장. 끝
끝 본문입니다.
""".strip()
        payload = {
            "filename": "계층테스트.txt",
            "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "destination": "new_project",
            "split": "toc",
            "project_title": "계층 임포트",
            "purpose": "general_novel",
            "main_genre": "판타지",
            "sub_genre": "",
        }
        st, result = self.request("POST", "/api/import", payload)
        self.assertEqual(st, 201)
        self.assertTrue(result.get("hierarchy"))
        pid = int(result["project_id"])

        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            self.assertTrue(
                folder_tree.project_folder_sync_complete(conn, pid),
                "import should sync folder tree for outline folder path",
            )

        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        part_titles = [p["title"] for p in outline["parts"]]
        self.assertEqual(part_titles[0], "목차")
        self.assertIn("1권", part_titles)
        vol = next(p for p in outline["parts"] if p["title"] == "1권")
        # Folders under volume in document order
        ch_titles = [c["title"] for c in vol["chapters"]]
        # Hierarchy plan uses 부/장 folders — expect 1부, 2부 or jang folders
        self.assertTrue(len(ch_titles) >= 1)
        # Collect all episode titles in binder order (parts → chapters → scenes_flat)
        episode_titles: list[str] = []
        for part in outline["parts"]:
            if part["title"] == "목차":
                continue
            for ch in part["chapters"]:
                for s in ch.get("scenes_flat") or []:
                    episode_titles.append(s["title"])
        # Key episodes appear in narrative order
        def idx(name: str) -> int:
            for i, t in enumerate(episode_titles):
                if name in t:
                    return i
            return -1

        i1, i2, i3 = idx("시작"), idx("중간"), idx("끝")
        self.assertGreaterEqual(i1, 0)
        self.assertGreaterEqual(i2, 0)
        self.assertGreaterEqual(i3, 0)
        self.assertLess(i1, i2)
        self.assertLess(i2, i3)

        # legacy vs folder path still match after import sync
        handler = object.__new__(app.SuperToryHandler)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            legacy = handler._project_outline_from_legacy(conn, pid)
            folder = handler._project_outline_from_folder(conn, pid)
        self.assertEqual(
            [p["title"] for p in legacy["parts"]],
            [p["title"] for p in folder["parts"]],
        )
        for lp, fp in zip(legacy["parts"], folder["parts"]):
            self.assertEqual(
                [c["title"] for c in lp["chapters"]],
                [c["title"] for c in fp["chapters"]],
            )
            for lc, fc in zip(lp["chapters"], fp["chapters"]):
                self.assertEqual(
                    [s["id"] for s in lc.get("scenes_flat") or []],
                    [s["id"] for s in fc.get("scenes_flat") or []],
                )

    def _folder_id_for_part(self, project_id: int, part_id: int) -> int:
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            fid = folder_tree.folder_id_for_source(
                conn, int(project_id), "part", int(part_id)
            )
            self.assertIsNotNone(fid)
            return int(fid)

    @staticmethod
    def _flat_scene_titles(nodes) -> list[str]:
        out: list[str] = []
        for n in nodes or []:
            out.append(n["title"])
            out.extend(FolderOutlineReadTests._flat_scene_titles(n.get("children") or []))
        return out

    def test_folders_tree_three_level_nest(self) -> None:
        """After reparent 부1>부2>부3, folders JSON is truly 3-deep."""
        st, project = self.request(
            "POST", "/api/projects", {"title": "3단 폴더", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        folder_ids = []
        for title in ("부1", "부2", "부3"):
            st, part = self.request(
                "POST", f"/api/projects/{pid}/parts", {"title": title}
            )
            self.assertEqual(st, 201)
            folder_ids.append(self._folder_id_for_part(pid, part["id"]))
        f1, f2, f3 = folder_ids
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

        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        self.assertIn("folders", outline)
        self.assertIn("outline_shape", outline)
        roots = outline["folders"]
        self.assertEqual(len(roots), 1, msg=roots)
        self.assertEqual(roots[0]["title"], "부1")
        self.assertEqual(int(roots[0]["id"]), f1)
        kids = roots[0].get("children") or []
        self.assertEqual(len(kids), 1)
        self.assertEqual(kids[0]["title"], "부2")
        self.assertEqual(int(kids[0]["id"]), f2)
        grand = kids[0].get("children") or []
        self.assertEqual(len(grand), 1)
        self.assertEqual(grand[0]["title"], "부3")
        self.assertEqual(int(grand[0]["id"]), f3)
        self.assertEqual(grand[0].get("children") or [], [])
        self.assertIn("parts", outline)
        self.assertIsInstance(outline["parts"], list)

    def test_folders_tree_matches_shallow_part_chapter(self) -> None:
        """Shallow part→chapter projects expose the same content via folders."""
        pid = self._seed_hierarchy()
        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        self.assertIn("folders", outline)
        folders = outline["folders"]
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0]["title"], "1권")
        self.assertTrue(folders[0].get("is_box"))
        ch_titles = [c["title"] for c in folders[0].get("children") or []]
        self.assertEqual(ch_titles, ["1장", "2장"])
        self.assertEqual(
            self._flat_scene_titles(folders[0]["children"][0].get("scenes")),
            ["씬A", "씬B"],
        )
        self.assertEqual(
            self._flat_scene_titles(folders[0]["children"][1].get("scenes")),
            ["씬C"],
        )
        self.assertEqual(outline["parts"][0]["title"], "1권")
        self.assertEqual(
            [c["title"] for c in outline["parts"][0]["chapters"]],
            ["1장", "2장"],
        )

    def test_folders_tree_bulk_query_no_n_plus_one(self) -> None:
        """Building folders uses a bounded number of folder/scene queries (not per node)."""
        pid = self._seed_hierarchy()
        st, outline0 = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        part_id = outline0["parts"][0]["id"]
        for i in range(5):
            st, _ch = self.request(
                "POST",
                f"/api/projects/{pid}/chapters",
                {"title": f"추가{i}", "part_id": part_id},
            )
            self.assertEqual(st, 201)

        queries: list[str] = []

        def tracer(sql):
            queries.append(" ".join(str(sql).split()))

        handler = object.__new__(app.SuperToryHandler)
        with app.database() as conn:
            conn.row_factory = __import__("sqlite3").Row
            conn.set_trace_callback(tracer)
            outline = handler._project_outline_from_folder(conn, pid)
            conn.set_trace_callback(None)

        self.assertIn("folders", outline)
        # Ignore readiness probes (SELECT 1 … LIMIT 1)
        folder_selects = [
            q
            for q in queries
            if "from folder" in q.lower()
            and q.lower().lstrip().startswith("select")
            and "limit 1" not in q.lower()
        ]
        scene_selects = [
            q
            for q in queries
            if "from scene" in q.lower()
            and q.lower().lstrip().startswith("select")
            and "limit 1" not in q.lower()
        ]
        # Legacy map (part/chapter/meta) + one bulk tree load — not one query per node.
        # With 7+ chapters, N+1 would exceed this comfortably.
        self.assertLessEqual(len(folder_selects), 4, msg=folder_selects)
        self.assertLessEqual(len(scene_selects), 2, msg=scene_selects)
        kids = outline["folders"][0].get("children") or []
        self.assertGreaterEqual(len(kids), 7)

    def test_untitled_word_paste_binder_preview_hides_html(self) -> None:
        st, project = self.request(
            "POST", "/api/projects", {"title": "미리보기 검증", "main_genre": "판타지"}
        )
        self.assertEqual(st, 201)
        pid = int(project["id"])
        st, chapter = self.request(
            "POST", f"/api/projects/{pid}/chapters", {"title": "1장"}
        )
        self.assertEqual(st, 201)
        style = (
            "box-sizing: border-box; color: rgb(10, 10, 10); "
            'font-family: Batang, "Apple Myungjo", serif; font-size: medium; '
        ) * 20
        html = (
            f'<span style="{style}">***</span>'
            f'<br style="{style}">'
            "이오나의 마음속은 외로움인지 그리움인지 알 수 없었다. 그는 창밖을 보았다."
        )
        br_close = html.find(">", html.find("<br"))
        self.assertGreater(br_close, 1200)

        st, untitled = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "새 씬"}
        )
        self.assertIn(st, (200, 201))
        st, titled = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화 제목"}
        )
        self.assertIn(st, (200, 201))
        st, short = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "새 씬"}
        )
        self.assertIn(st, (200, 201))

        for scene, body in (
            (untitled, html),
            (titled, html),
            (short, "테스트88888<div>테스트</div><div><br></div>"),
        ):
            st, detail = self.request("GET", f"/api/scenes/{scene['id']}")
            self.assertEqual(st, 200)
            st, saved = self.request(
                "PUT",
                f"/api/scenes/{scene['id']}",
                {
                    "title": scene["title"],
                    "content_md": body,
                    "row_version": detail["row_version"],
                },
            )
            self.assertEqual(st, 200, saved)

        st, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        by_id = {
            int(s["id"]): s
            for ch in outline.get("chapters") or []
            for s in ch.get("scenes_flat") or []
        }
        untitled_row = by_id[int(untitled["id"])]
        titled_row = by_id[int(titled["id"])]
        short_row = by_id[int(short["id"])]

        self.assertEqual(untitled_row["title"], "새 씬")
        preview = untitled_row.get("body_preview") or ""
        self.assertIn("이오나의 마음속은", preview)
        self.assertNotIn("<br", preview)
        self.assertNotIn("style=", preview)
        self.assertNotIn("box-sizing", preview)

        self.assertEqual(titled_row["title"], "1화 제목")
        self.assertEqual(titled_row.get("body_preview") or "", "")

        self.assertEqual(short_row.get("body_preview") or "", "테스트88888테스트")


if __name__ == "__main__":
    unittest.main()
