"""Tory dictionary CRUD, name-overlap warnings, and UI contracts."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
from world_import_analysis import compose_worldbuilding_md

ROOT = Path(__file__).resolve().parents[1]


class CustomDictionaryTests(unittest.TestCase):
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
        status, project = self.request("POST", "/api/projects", {"title": "사전 작품", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def test_migration_89_on_init(self) -> None:
        with app.database() as connection:
            name = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 89"
            ).fetchone()
            self.assertEqual(name[0], "custom_dictionary_terms")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'custom_dictionary_terms'"
            ).fetchone()
            self.assertIsNotNone(exists)

    def test_create_list_update_delete(self) -> None:
        pid = self._project()
        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/dictionary-terms",
            {"term": "에테르", "definition": "마나의 다른 이름", "memo": "고유어"},
        )
        self.assertEqual(status, 201, created)
        term_id = int(created["id"])
        self.assertEqual(created["term"], "에테르")
        self.assertEqual(created["memo"], "고유어")
        self.assertEqual(created["conflicts"], [])
        status, listed = self.request("GET", f"/api/projects/{pid}/dictionary-terms")
        self.assertEqual(status, 200, listed)
        self.assertEqual(listed[0]["term"], "에테르")
        status, updated = self.request(
            "PUT",
            f"/api/dictionary-terms/{term_id}",
            {"definition": "대기를 채운 마력", "memo": ""},
        )
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["definition"], "대기를 채운 마력")
        self.assertIsNone(updated["memo"])
        status, deleted = self.request("DELETE", f"/api/dictionary-terms/{term_id}")
        self.assertEqual(status, 200, deleted)
        status, listed = self.request("GET", f"/api/projects/{pid}/dictionary-terms")
        self.assertEqual(listed, [])

    def test_overlap_warns_but_still_saves(self) -> None:
        pid = self._project()
        status, character = self.request("POST", f"/api/projects/{pid}/characters", {"name": "리아"})
        self.assertEqual(status, 201, character)
        status, item = self.request("POST", f"/api/projects/{pid}/items", {"name": "흑염검"})
        self.assertEqual(status, 201, item)
        with app.database() as connection:
            md = compose_worldbuilding_md({"special": "에테르\n마석은 희귀하다"})
            connection.execute("UPDATE project SET worldbuilding_md = ? WHERE id = ?", (md, pid))
        status, as_item = self.request(
            "POST",
            f"/api/projects/{pid}/dictionary-terms",
            {"term": "흑염검", "definition": "검은 칼"},
        )
        self.assertEqual(status, 201, as_item)
        self.assertIn("item", as_item["conflicts"])
        status, as_char = self.request(
            "POST",
            f"/api/projects/{pid}/dictionary-terms",
            {"term": "리아", "definition": "주인공 이름과 같음"},
        )
        self.assertEqual(status, 201, as_char)
        self.assertIn("character", as_char["conflicts"])
        status, as_world = self.request(
            "POST",
            f"/api/projects/{pid}/dictionary-terms",
            {"term": "에테르", "definition": "세계관 용어"},
        )
        self.assertEqual(status, 201, as_world)
        self.assertIn("world", as_world["conflicts"])
        status, listed = self.request("GET", f"/api/projects/{pid}/dictionary-terms")
        self.assertEqual(len(listed), 3)

    def test_empty_term_rejected(self) -> None:
        pid = self._project()
        status, payload = self.request(
            "POST",
            f"/api/projects/{pid}/dictionary-terms",
            {"term": "   ", "definition": "빈 단어"},
        )
        self.assertEqual(status, 400, payload)

    def test_ui_has_dictionary_surfaces(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        ko = (ROOT / "web" / "locales" / "ko.json").read_text(encoding="utf-8")
        en = (ROOT / "web" / "locales" / "en.json").read_text(encoding="utf-8")
        es = (ROOT / "web" / "locales" / "es.json").read_text(encoding="utf-8")
        self.assertIn('data-settings-section="dictionary"', html)
        self.assertIn('id="dictionaryBoard"', html)
        self.assertIn('id="dictionaryModal"', html)
        self.assertIn('data-context-action="add-tory-dict"', html)
        self.assertIn('data-dock-item="dictionary"', html)
        self.assertIn("function openDictionaryBoard", app_js)
        self.assertIn("function addToryDictionaryFromSelection", app_js)
        self.assertIn("/api/projects/${state.projectId}/dictionary-terms", app_js)
        items_idx = html.find('data-settings-section="items"')
        dict_idx = html.find('data-settings-section="dictionary"')
        baits_idx = html.find('data-settings-section="baits"')
        self.assertLess(items_idx, dict_idx)
        self.assertLess(dict_idx, baits_idx)
        lookup = html.find('data-context-action="lookup-dict"')
        similar = html.find('data-context-action="similar-words"')
        add_dict = html.find('data-context-action="add-tory-dict"')
        cross = html.find('data-context-action="cross-ref-search"')
        self.assertLess(lookup, similar)
        self.assertLess(similar, add_dict)
        self.assertLess(add_dict, cross)
        self.assertNotIn('data-context-action="toggle-smart-punctuation"', html)
        highlight = html.find('data-context-action="toggle-dict-highlight"')
        self.assertGreater(highlight, 0)
        self.assertIn('id="dictHighlightMenuItem"', html)
        self.assertIn("id=\"smartPunctAdminBox\"", html)
        self.assertIn("id=\"smartPunctQuotes\"", html)
        self.assertIn('data-smart-punct="paren"', html)
        self.assertIn('data-ctx-selection-ok', html[highlight:highlight + 180])
        self.assertIn('id="dictTermPopup"', html)
        self.assertIn('id="dictTermPopupEdit"', html)
        self.assertIn("function toggleDictHighlight", app_js)
        self.assertIn("supertory.dictHighlight.", app_js)
        self.assertIn("mark.dict-term-hl", app_js)
        self.assertIn("st-dict-terms", app_js)
        self.assertIn("dict-term-hl", app_js)
        self.assertIn("scheduleDictHighlightRefresh", app_js)
        self.assertIn("::highlight(st-dict-terms)", css)
        self.assertIn("mark.dict-term-hl", css)
        self.assertIn(".context-menu-check {", css)
        self.assertIn("min-width: 1em;", css)
        self.assertIn('.context-menu-check::before {\n  content: "✓";\n  visibility: hidden;', css)
        for locale in (ko, en, es):
            self.assertIn("index.고유어_표시", locale)
            self.assertIn("index.사전에서_수정", locale)
            self.assertIn("index.고유어_표시를_켰어요", locale)
        for locale in (ko, en, es):
            self.assertIn('"app.사전"', locale)
        self.assertIn("dictionary_index", app_js)
        self.assertIn("dictionary_definition", app_js)
        self.assertIn('option value="dictionary"', html)

    def test_definition_map_covers_term_and_slash_parts(self) -> None:
        pid = self._project()
        status, _ = self.request(
            "POST",
            f"/api/projects/{pid}/dictionary-terms",
            {"term": "에테르", "definition": "마나"},
        )
        self.assertEqual(status, 201)
        status, _ = self.request(
            "POST",
            f"/api/projects/{pid}/dictionary-terms",
            {"term": "알파 / 베타", "definition": "쌍둥이"},
        )
        self.assertEqual(status, 201)
        with app.database() as connection:
            import custom_dictionary
            mapped = custom_dictionary.definition_map(connection, pid)
        self.assertEqual(mapped.get("에테르"), "마나")
        self.assertEqual(mapped.get("알파"), "쌍둥이")
        self.assertEqual(mapped.get("베타"), "쌍둥이")


if __name__ == "__main__":
    unittest.main()
