"""Tests for manuscript export packages (especially Hangul HWPX)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import app
import document_export
import document_import


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
        status, project = self.request_json("POST", "/api/projects", {"title": "앱한글내보내기"})
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


if __name__ == "__main__":
    unittest.main()
