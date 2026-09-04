"""Character story role (주연/적대자/조력자/단역) + nullable 미지정."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parents[1]


class CharacterRoleTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _project(self) -> int:
        status, project = self.request("POST", "/api/projects", {"title": "역할 테스트", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def test_migration_allows_null_role(self) -> None:
        with app.database() as connection:
            row = connection.execute("PRAGMA table_info(character)").fetchall()
            role_col = next((item for item in row if str(item[1]) == "role"), None)
            self.assertIsNotNone(role_col)
            self.assertEqual(int(role_col[3]), 0)
            version = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 71"
            ).fetchone()
            self.assertEqual(version[0], "character_role_nullable")

    def test_new_character_defaults_to_unspecified(self) -> None:
        pid = self._project()
        status, created = self.request("POST", f"/api/projects/{pid}/characters", {"name": "이름만"})
        self.assertEqual(status, 201, created)
        status, listed = self.request("GET", f"/api/projects/{pid}/characters")
        self.assertEqual(status, 200, listed)
        self.assertEqual(listed[0]["name"], "이름만")
        self.assertTrue(listed[0].get("role") in (None, ""))
        status, detail = self.request("GET", f"/api/characters/{created['id']}")
        self.assertEqual(status, 200, detail)
        self.assertTrue((detail["character"].get("role") or "") in (None, ""))

    def test_save_role_and_clear(self) -> None:
        pid = self._project()
        status, created = self.request("POST", f"/api/projects/{pid}/characters", {"name": "리아"})
        cid = int(created["id"])
        status, detail = self.request("GET", f"/api/characters/{cid}")
        version = detail["character"]["row_version"]
        status, _ = self.request(
            "PUT",
            f"/api/characters/{cid}",
            {"name": "리아", "role": "주연", "row_version": version},
        )
        self.assertEqual(status, 200)
        status, detail = self.request("GET", f"/api/characters/{cid}")
        self.assertEqual(detail["character"]["role"], "protagonist")
        version = detail["character"]["row_version"]
        status, _ = self.request(
            "PUT",
            f"/api/characters/{cid}",
            {"name": "리아", "role": "", "row_version": version},
        )
        self.assertEqual(status, 200)
        status, detail = self.request("GET", f"/api/characters/{cid}")
        self.assertTrue((detail["character"].get("role") or "") in (None, ""))
        status, listed = self.request("GET", f"/api/projects/{pid}/characters")
        self.assertTrue((listed[0].get("role") or "") in (None, ""))

    def test_save_korean_aliases(self) -> None:
        pid = self._project()
        status, created = self.request("POST", f"/api/projects/{pid}/characters", {"name": "적"})
        cid = int(created["id"])
        status, detail = self.request("GET", f"/api/characters/{cid}")
        version = detail["character"]["row_version"]
        status, _ = self.request(
            "PUT",
            f"/api/characters/{cid}",
            {"name": "적", "role": "적대자", "row_version": version},
        )
        self.assertEqual(status, 200)
        status, detail = self.request("GET", f"/api/characters/{cid}")
        self.assertEqual(detail["character"]["role"], "antagonist")

    def test_save_custom_role(self) -> None:
        pid = self._project()
        status, created = self.request("POST", f"/api/projects/{pid}/characters", {"name": "세린"})
        cid = int(created["id"])
        status, detail = self.request("GET", f"/api/characters/{cid}")
        version = detail["character"]["row_version"]
        status, _ = self.request(
            "PUT",
            f"/api/characters/{cid}",
            {"name": "세린", "role": "멘토", "row_version": version},
        )
        self.assertEqual(status, 200)
        status, detail = self.request("GET", f"/api/characters/{cid}")
        self.assertEqual(detail["character"]["role"], "멘토")
        version = detail["character"]["row_version"]
        status, bad = self.request(
            "PUT",
            f"/api/characters/{cid}",
            {"name": "세린", "role": "x" * 41, "row_version": version},
        )
        self.assertEqual(status, 400, bad)

    def test_migration_allows_custom_role(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'character'"
            ).fetchone()
            sql = str(row[0] or "")
            self.assertIn("length(trim(role)) BETWEEN 1 AND 40", sql)
            self.assertNotIn("role IN ('protagonist'", sql)
            version = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 85"
            ).fetchone()
            self.assertEqual(version[0], "character_custom_roles")

    def test_ui_has_chips_and_group_view(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="characterRoleChips"', html)
        self.assertIn('data-character-role="protagonist"', html)
        self.assertIn('data-character-role="antagonist"', html)
        self.assertIn('data-character-role="supporting"', html)
        self.assertIn('data-character-role="minor"', html)
        self.assertIn('id="characterRoleAddButton"', html)
        self.assertNotIn('<select id="characterRole"', html)
        sort_at = html.find('id="characterSortName"')
        chips_at = html.find('id="characterRoleChips"')
        summary_at = html.find('id="characterSummary"')
        self.assertTrue(0 < sort_at < chips_at < summary_at)
        self.assertIn('id="characterRoleFilterRow"', html)
        self.assertIn('id="characterBoardGroupToggle"', html)
        self.assertIn("function renderCharacterRoleFilterRow", app_js)
        self.assertIn("function promptAddCharacterRole", app_js)
        self.assertIn("characterBoardGroupView", app_js)
        self.assertIn("unspecified", app_js)


if __name__ == "__main__":
    unittest.main()
