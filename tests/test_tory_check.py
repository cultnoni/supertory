"""Live Tory Check: per-project SQLite settings + analysis engine contracts."""

from __future__ import annotations

import http.client
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import app


ROOT = Path(__file__).resolve().parent.parent


class ToryCheckLogicTests(unittest.TestCase):
    def test_node_analysis_engine(self) -> None:
        script = ROOT / "tests" / "tory_check_logic.js"
        result = subprocess.run(
            ["node", str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)


class ToryCheckApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.engine = (ROOT / "web" / "tory-check.js").read_text(encoding="utf-8")
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

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
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def create_project(self, title: str) -> int:
        status, project = self.request("POST", "/api/projects", {"title": title, "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def test_defaults_and_per_project_isolation(self) -> None:
        first = self.create_project("작품 하나")
        second = self.create_project("작품 둘")
        status, data = self.request("GET", f"/api/projects/{first}/tory-check")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["preset"], "normal")
        self.assertIsNone(data["viewpoint_person"])
        self.assertIsNone(data["viewpoint_tense"])
        self.assertEqual(data["forbidden_words"], [])

        status, saved = self.request(
            "PUT",
            f"/api/projects/{first}/tory-check",
            {
                "preset": "strict",
                "viewpoint_person": "first",
                "viewpoint_tense": "past",
                "forbidden_words": ["금지", "비밀"],
            },
        )
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["preset"], "strict")
        self.assertEqual(saved["viewpoint_person"], "first")
        self.assertEqual(saved["forbidden_words"], ["금지", "비밀"])

        status, other = self.request("GET", f"/api/projects/{second}/tory-check")
        self.assertEqual(status, 200, other)
        self.assertEqual(other["preset"], "normal")
        self.assertEqual(other["forbidden_words"], [])
        self.assertIsNone(other["viewpoint_person"])

        status, again = self.request("GET", f"/api/projects/{first}/tory-check")
        self.assertEqual(status, 200, again)
        self.assertEqual(again["preset"], "strict")
        self.assertEqual(again["forbidden_words"], ["금지", "비밀"])

    def test_partial_put_keeps_other_fields(self) -> None:
        pid = self.create_project("부분 저장")
        self.request(
            "PUT",
            f"/api/projects/{pid}/tory-check",
            {"preset": "loose", "forbidden_words": ["금칙"]},
        )
        status, saved = self.request(
            "PUT",
            f"/api/projects/{pid}/tory-check",
            {"viewpoint_person": "third", "viewpoint_tense": "present"},
        )
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["preset"], "loose")
        self.assertEqual(saved["forbidden_words"], ["금칙"])
        self.assertEqual(saved["viewpoint_person"], "third")
        self.assertEqual(saved["viewpoint_tense"], "present")

    def test_migration_recorded(self) -> None:
        with app.database() as connection:
            name = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 86"
            ).fetchone()[0]
            self.assertEqual(name, "project_tory_check")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("project_tory_check", tables)

    def test_engine_and_widget_contracts(self) -> None:
        self.assertIn("const DEBOUNCE_MS = 400", self.engine)
        self.assertIn("const WINDOW_CHARS = 1000", self.engine)
        self.assertIn('DEFAULT_TAB = "words"', self.engine)
        self.assertIn("function analyze(tabId, text, settings)", self.engine)
        self.assertIn('src="/tory-check.js', self.html)
        self.assertLess(
            self.html.find('src="/tory-check.js'),
            self.html.find('src="/app.js'),
        )
        self.assertIn("function runToryCheckActiveTab(", self.js)
        self.assertIn("engine.analyze(tab, getEditorPlainText()", self.js)
        self.assertIn('id="toryCheckViewpointForm"', self.html)
        self.assertIn('class="continue-length-options tory-helper-options"', self.html)
        self.assertIn('name="toryCheckPerson"', self.html)
        self.assertIn('name="toryCheckTense"', self.html)
        self.assertNotIn("tory-check-seg-btn", self.html)


class ToryCheckSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = __import__("sqlite3").connect(":memory:")
        self.db.execute("PRAGMA foreign_keys = ON")
        schema = ROOT / "db" / "001_initial_schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        self.db.execute("INSERT INTO project(id, title) VALUES (1, 'A')")
        self.db.execute("INSERT INTO project(id, title) VALUES (2, 'B')")

    def tearDown(self) -> None:
        self.db.close()

    def test_project_tory_check_table(self) -> None:
        migration = ROOT / "db" / "086_project_tory_check.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        self.db.execute(
            "INSERT INTO project_tory_check(project_id, preset, viewpoint_person, viewpoint_tense, forbidden_words_json) "
            "VALUES (1, 'normal', 'first', 'past', '[\"비밀\"]')"
        )
        row = self.db.execute(
            "SELECT preset, forbidden_words_json FROM project_tory_check WHERE project_id = 1"
        ).fetchone()
        self.assertEqual(row[0], "normal")
        self.assertEqual(json.loads(row[1]), ["비밀"])
        with self.assertRaises(__import__("sqlite3").IntegrityError):
            self.db.execute(
                "INSERT INTO project_tory_check(project_id, preset) VALUES (1, 'strict')"
            )
        with self.assertRaises(__import__("sqlite3").IntegrityError):
            self.db.execute(
                "INSERT INTO project_tory_check(project_id, preset) VALUES (3, 'normal')"
            )
        version = self.db.execute(
            "SELECT name FROM schema_migration WHERE version = 86"
        ).fetchone()[0]
        self.assertEqual(version, "project_tory_check")


if __name__ == "__main__":
    unittest.main()
