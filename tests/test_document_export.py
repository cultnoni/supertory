"""Tests for manuscript export packages (especially Hangul HWPX)."""

from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import app
import document_export
import document_import
import folder_tree


class HwpxExportUnitTests(unittest.TestCase):
    def test_hwpx_package_is_hangul_shaped_and_roundtrips(self) -> None:
        exported = document_export.export_bytes(
            "hwpx",
            project_title="한글열기테스트",
            chapters=[
                {
                    "title": "1장",
                    "scenes": [
                        {
                            "title": "첫 씬",
                            "content_plain": "바람이 불었다.\n\n문이 천천히 열렸다.",
                        }
                    ],
                }
            ],
        )
        self.assertTrue(exported.filename.endswith(".hwpx"))
        self.assertEqual(exported.mime, "application/hwp+zip")
        self.assertEqual(document_export.validate_hwpx_package(exported.data), [])

        with zipfile.ZipFile(__import__("io").BytesIO(exported.data)) as archive:
            self.assertEqual(archive.namelist()[0], "mimetype")
            self.assertEqual(archive.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            header = archive.read("Contents/header.xml").decode("utf-8")
            section = archive.read("Contents/section0.xml").decode("utf-8")
            self.assertIn("<hh:charPr", header)
            self.assertIn("<hh:paraPr", header)
            self.assertIn("<hp:secPr", section)
            self.assertNotIn("\n", section)
            self.assertIn("바람이 불었다.", section)

        extracted = document_import.extract_document(exported.filename, exported.data)
        self.assertIn("바람이 불었다.", extracted.text)
        self.assertIn("문이 천천히 열렸다.", extracted.text)

    def test_hwpx_preserves_blank_lines_between_paragraphs(self) -> None:
        """Blank lines (\\n\\n) become empty hp:p slots between text lines, not at ends."""
        body = 'A\n\n"B"\n\nC'
        lines = document_export._hwpx_paragraph_lines(
            [document_export.ManuscriptBlock("body", body)]
        )
        # Blank separators between A | "B" | C (not at ends).
        self.assertEqual(lines, ["A", "", '"B"', "", "C"])
        self.assertNotEqual(lines[0], "")
        self.assertNotEqual(lines[-1], "")
        # Consecutive blank runs collapse to a single empty paragraph.
        multi = document_export._hwpx_paragraph_lines(
            [document_export.ManuscriptBlock("body", "X\n\n\n\nY")]
        )
        self.assertEqual(multi, ["X", "", "Y"])

        exported = document_export.export_bytes(
            "hwpx",
            project_title="빈줄",
            chapters=[
                {
                    "title": "1장",
                    "scenes": [{"title": "씬", "content_plain": body}],
                }
            ],
        )
        self.assertEqual(document_export.validate_hwpx_package(exported.data), [])
        with zipfile.ZipFile(__import__("io").BytesIO(exported.data)) as archive:
            section = archive.read("Contents/section0.xml").decode("utf-8")
        # title + chapter + scene + 5 body lines (3 text + 2 empty) = many hp:p
        self.assertGreaterEqual(section.count("<hp:p "), 8)
        self.assertIn("<hp:t/>", section)
        self.assertIn(">A</hp:t>", section)
        # Quotes are XML-escaped in HWPX text nodes.
        self.assertIn(">&quot;B&quot;</hp:t>", section)
        self.assertIn(">C</hp:t>", section)

    def test_docx_preserves_blank_lines_between_paragraphs(self) -> None:
        """Blank lines (\\n\\n) become empty w:p between text; trailing empty kept."""
        import re

        def para_texts(docx_bytes: bytes) -> list[str]:
            with zipfile.ZipFile(__import__("io").BytesIO(docx_bytes)) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
            texts: list[str] = []
            for p in re.findall(r"<w:p>.*?</w:p>", xml):
                parts = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", p)
                joined = "".join(
                    t.replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&amp;", "&")
                    .replace("&quot;", '"')
                    .replace("&apos;", "'")
                    for t in parts
                )
                texts.append(joined)
            return texts

        body = 'A\n\n"B"\n\nC'
        data = document_export.build_docx(
            [document_export.ManuscriptBlock("body", body)]
        )
        texts = para_texts(data)
        # [A, empty, "B", empty, C, trailing empty]
        self.assertEqual(texts, ["A", "", '"B"', "", "C", ""])

        multi = document_export.build_docx(
            [document_export.ManuscriptBlock("body", "X\n\n\n\nY")]
        )
        multi_texts = para_texts(multi)
        self.assertEqual(multi_texts, ["X", "", "Y", ""])

        # Soft break inside a part still works (single \n, not blank line).
        soft = document_export.build_docx(
            [document_export.ManuscriptBlock("body", "line1\nline2")]
        )
        with zipfile.ZipFile(__import__("io").BytesIO(soft)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("<w:br/>", xml)
        soft_texts = para_texts(soft)
        # One content paragraph (with br) + trailing empty
        self.assertEqual(len(soft_texts), 2)
        self.assertEqual(soft_texts[0], "line1line2")
        self.assertEqual(soft_texts[1], "")

    def test_partial_export_omits_document_title_heading(self) -> None:
        chapters = [
            {
                "title": "1장",
                "scenes": [{"title": "회차A", "content_plain": "본문이다."}],
            }
        ]
        with_title = document_export.build_blocks(
            project_title="작품 - 선택회차2",
            chapters=chapters,
            include_title=True,
        )
        self.assertEqual(with_title[0].kind, "title")
        self.assertIn("선택회차", with_title[0].text)

        without = document_export.build_blocks(
            project_title="작품 - 선택회차2",
            chapters=chapters,
            include_title=False,
        )
        self.assertTrue(without)
        self.assertNotEqual(without[0].kind, "title")
        plain = document_export.blocks_to_plain(without)
        self.assertNotIn("선택회차", plain)
        self.assertIn("본문이다.", plain)

        exported = document_export.export_bytes(
            "txt",
            project_title="파일명용",
            chapters=chapters,
            include_title=False,
        )
        text = exported.data.decode("utf-8-sig")
        self.assertNotIn("선택회차", text)
        self.assertNotIn("파일명용", text)
        self.assertIn("회차A", text)


class HwpxExportApiTests(unittest.TestCase):
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

    def request_raw(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, bytes, dict[str, str]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = response.read()
        header_map = {k.lower(): v for k, v in response.getheaders()}
        status = response.status
        connection.close()
        return status, data, header_map

    def request_json(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        status, data, _ = self.request_raw(method, path, payload)
        return status, json.loads(data.decode("utf-8"))

    def test_export_hwpx_via_api_download(self) -> None:
        status, project = self.request_json(
            "POST",
            "/api/projects",
            {"title": "앱한글내보내기", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201)
        pid = project["id"]
        status, chapter = self.request_json(
            "POST", f"/api/projects/{pid}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201)
        status, scene = self.request_json(
            "POST",
            f"/api/chapters/{chapter['id']}/scenes",
            {"title": "회차1"},
        )
        self.assertEqual(status, 201)
        status, detail = self.request_json("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        status, _ = self.request_json(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "content_md": "<p>한글에서 열려야 하는 본문입니다.</p>",
                "title": "회차1",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200)

        status, data, headers = self.request_raw(
            "POST",
            f"/api/projects/{pid}/export",
            {"format": "hwpx", "save_to_folder": False},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.startswith(b"PK"), data[:200])
        self.assertEqual(document_export.validate_hwpx_package(data), [])
        disposition = headers.get("content-disposition", "")
        self.assertIn(".hwpx", disposition.lower())
        extracted = document_import.extract_document("out.hwpx", data)
        self.assertIn("한글에서 열려야 하는 본문입니다.", extracted.text)

    def test_selected_scenes_export_has_no_선택회차_heading(self) -> None:
        status, project = self.request_json(
            "POST",
            "/api/projects",
            {"title": "선택내보내기", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201)
        pid = project["id"]
        status, chapter = self.request_json(
            "POST", f"/api/projects/{pid}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201)
        scene_ids = []
        for i, body in enumerate(("첫째 본문", "둘째 본문"), start=1):
            status, scene = self.request_json(
                "POST",
                f"/api/chapters/{chapter['id']}/scenes",
                {"title": f"회차{i}"},
            )
            self.assertEqual(status, 201)
            scene_ids.append(scene["id"])
            status, detail = self.request_json("GET", f"/api/scenes/{scene['id']}")
            self.assertEqual(status, 200)
            status, _ = self.request_json(
                "PUT",
                f"/api/scenes/{scene['id']}",
                {
                    "content_md": f"<p>{body}</p>",
                    "title": f"회차{i}",
                    "row_version": detail["row_version"],
                },
            )
            self.assertEqual(status, 200)

        status, data, _ = self.request_raw(
            "POST",
            f"/api/projects/{pid}/export",
            {
                "format": "txt",
                "scene_ids": scene_ids,
                "title": "2개 회차",
                "save_to_folder": False,
            },
        )
        self.assertEqual(status, 200)
        text = data.decode("utf-8-sig")
        self.assertNotIn("선택회차", text)
        self.assertNotIn("2개 회차", text)
        self.assertIn("첫째 본문", text)
        self.assertIn("둘째 본문", text)

    def test_full_export_follows_binder_folder_order(self) -> None:
        """Sibling folder.sort_order wins over stale chapter.sort_order."""
        status, project = self.request_json(
            "POST",
            "/api/projects",
            {"title": "내보내기순서", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201)
        pid = int(project["id"])
        status, volume = self.request_json(
            "POST", f"/api/projects/{pid}/parts", {"title": "1권"}
        )
        self.assertEqual(status, 201)
        # Create 추가확인 first so chapter.sort_order stays ahead of 1부.
        status, extra = self.request_json(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "추가확인", "part_id": volume["id"]},
        )
        self.assertEqual(status, 201)
        status, part = self.request_json(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "1부", "part_id": volume["id"]},
        )
        self.assertEqual(status, 201)

        for chapter, heading, body in (
            (extra, "추가확인", "추가확인 본문"),
            (part, "1부", "1부 본문"),
        ):
            status, scene = self.request_json(
                "POST",
                f"/api/chapters/{chapter['id']}/scenes",
                {"title": f"{heading} 회차"},
            )
            self.assertEqual(status, 201)
            status, detail = self.request_json("GET", f"/api/scenes/{scene['id']}")
            self.assertEqual(status, 200)
            status, _ = self.request_json(
                "PUT",
                f"/api/scenes/{scene['id']}",
                {
                    "content_md": f"<p>{body}</p>",
                    "title": f"{heading} 회차",
                    "row_version": detail["row_version"],
                },
            )
            self.assertEqual(status, 200)

        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            extra_folder = folder_tree.folder_id_for_source(
                conn, pid, "chapter", int(extra["id"])
            )
            part_folder = folder_tree.folder_id_for_source(
                conn, pid, "chapter", int(part["id"])
            )
            self.assertIsNotNone(extra_folder)
            self.assertIsNotNone(part_folder)
            ch_rows = conn.execute(
                "SELECT title, sort_order FROM chapter "
                "WHERE project_id = ? AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (pid,),
            ).fetchall()
            self.assertEqual(
                [row["title"] for row in ch_rows],
                ["추가확인", "1부"],
                "precondition: chapter.sort_order still has 추가확인 first",
            )

        # Binder reorder: 1권 > 1부 > 추가확인 (folder.sort_order only).
        status, moved = self.request_json(
            "POST",
            f"/api/folders/{part_folder}/reparent",
            {"position": "before", "target_id": extra_folder},
        )
        self.assertEqual(status, 200, moved)

        status, outline = self.request_json("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(status, 200)
        vol = (outline.get("folders") or [None])[0]
        self.assertIsNotNone(vol)
        self.assertEqual(vol.get("title"), "1권")
        binder_titles = [c.get("title") for c in (vol.get("children") or [])]
        self.assertEqual(binder_titles, ["1부", "추가확인"])

        status, data, _ = self.request_raw(
            "POST",
            f"/api/projects/{pid}/export",
            {"format": "txt", "save_to_folder": False},
        )
        self.assertEqual(status, 200)
        text = data.decode("utf-8-sig")
        self.assertLess(text.find("1부"), text.find("추가확인"), text)
        self.assertLess(text.find("1부 본문"), text.find("추가확인 본문"), text)


if __name__ == "__main__":
    unittest.main()
