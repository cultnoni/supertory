"""Tests for Scrivener-style .stg project packages."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import project_package


class ProjectPackageUnitTests(unittest.TestCase):
    def test_write_and_read_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = project_package.create_or_update_package(
                root,
                project_uuid="11111111-2222-3333-4444-555555555555",
                title="선밖에서",
                purpose="novel",
                project_id=7,
            )
            self.assertTrue(path.name.endswith(".stg"))
            self.assertEqual(path.name, "선밖에서.stg")
            data = project_package.read_package(path)
            self.assertEqual(data["format"], "supertory-project")
            self.assertEqual(data["uuid"], "11111111-2222-3333-4444-555555555555")
            self.assertEqual(data["title"], "선밖에서")

    def test_safe_filename_strips_invalid_chars(self) -> None:
        self.assertEqual(project_package.safe_filename('A<>:"/\\|?*B'), "AB")

    def test_reads_legacy_storyguide_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "old.stg"
            path.write_text(
                json.dumps(
                    {
                        "format": "storyguide-project",
                        "version": 1,
                        "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "title": "레거시",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            data = project_package.read_package(path)
            self.assertEqual(data["uuid"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            self.assertEqual(data["format"], "storyguide-project")


class ProjectPackageApiTests(unittest.TestCase):
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
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_create_project_writes_stg_file(self) -> None:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "선밖에서", "purpose": "novel", "main_genre": "판타지"},
        )
        self.assertEqual(status, 201)
        self.assertIn("package_path", project)
        self.assertTrue(project["package_name"].endswith(".stg"))
        package = Path(project["package_path"])
        self.assertTrue(package.is_file())
        data = project_package.read_package(package)
        self.assertEqual(data["title"], "선밖에서")

        project_id = app.resolve_package_file(package)
        self.assertEqual(project_id, project["id"])

        status, projects = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual(projects[0]["uuid"], data["uuid"])


if __name__ == "__main__":
    unittest.main()
