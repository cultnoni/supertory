"""Item CRUD, aliases, owner, and UI contracts."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parents[1]


class ItemApiTests(unittest.TestCase):
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
        status, project = self.request("POST", "/api/projects", {"title": "아이템 작품", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def test_migration_73_on_init(self) -> None:
        with app.database() as connection:
            name = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 73"
            ).fetchone()
            self.assertEqual(name[0], "item")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'item'"
            ).fetchone()
            self.assertIsNotNone(exists)

    def test_create_list_update_alias_delete(self) -> None:
        pid = self._project()
        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/items",
            {"name": "흑염검", "description": "검은 칼날"},
        )
        self.assertEqual(status, 201, created)
        item_id = int(created["id"])
        status, listed = self.request("GET", f"/api/projects/{pid}/items")
        self.assertEqual(status, 200, listed)
        self.assertEqual(listed[0]["name"], "흑염검")
        self.assertEqual(listed[0]["description"], "검은 칼날")
        status, alias = self.request(
            "POST",
            f"/api/items/{item_id}/aliases",
            {"alias": "흑검"},
        )
        self.assertEqual(status, 201, alias)
        status, detail = self.request("GET", f"/api/items/{item_id}")
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["item"]["name"], "흑염검")
        self.assertEqual({row["alias"] for row in detail["aliases"]}, {"흑검"})
        version = detail["item"]["row_version"]
        status, _ = self.request(
            "PUT",
            f"/api/items/{item_id}",
            {
                "name": "흑염검",
                "description": "달빛을 먹는 검",
                "aliases": ["흑검", "달빛검"],
                "row_version": version,
            },
        )
        self.assertEqual(status, 200)
        status, detail = self.request("GET", f"/api/items/{item_id}")
        self.assertEqual(detail["item"]["description"], "달빛을 먹는 검")
        self.assertEqual({row["alias"] for row in detail["aliases"]}, {"흑검", "달빛검"})
        status, trashed = self.request("DELETE", f"/api/items/{item_id}")
        self.assertEqual(status, 200, trashed)
        status, listed = self.request("GET", f"/api/projects/{pid}/items")
        self.assertEqual(listed, [])

    def test_owner_link_and_character_trash_clears_owner(self) -> None:
        pid = self._project()
        status, character = self.request("POST", f"/api/projects/{pid}/characters", {"name": "리아"})
        self.assertEqual(status, 201, character)
        cid = int(character["id"])
        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/items",
            {"name": "리아의 반지", "owner_character_id": cid},
        )
        self.assertEqual(status, 201, created)
        item_id = int(created["id"])
        status, detail = self.request("GET", f"/api/items/{item_id}")
        self.assertEqual(detail["item"]["owner_character_id"], cid)
        self.assertEqual(detail["item"]["owner_name"], "리아")
        status, _ = self.request("DELETE", f"/api/characters/{cid}")
        self.assertEqual(status, 200)
        status, detail = self.request("GET", f"/api/items/{item_id}")
        self.assertIsNone(detail["item"]["owner_character_id"])

    def test_ui_has_item_board_and_editor(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-settings-section="items"', html)
        self.assertIn('id="itemBoard"', html)
        self.assertIn('id="itemEditor"', html)
        self.assertIn('id="itemAliasList"', html)
        self.assertIn('id="itemOwner"', html)
        self.assertIn('id="sceneItemCandidatePanel"', html)
        self.assertIn('data-world-field="heritage"', html)
        self.assertIn("function openItemBoard", app_js)
        self.assertIn("watchSceneItemAnalysis", app_js)
        self.assertIn("heritage", app_js)
        self.assertIn("syncItemToriDraftStyle", app_js)
        self.assertIn('id="itemChronicle"', html)
        self.assertIn('id="itemChronicleButton"', html)
        self.assertIn("toggleItemChronicle", app_js)
        self.assertIn("toggleTraitChronicle", app_js)
        self.assertIn("renderTraitChronicleList", app_js)
        self.assertIn("/api/items/${state.itemId}/trait-history", app_js)


if __name__ == "__main__":
    unittest.main()
