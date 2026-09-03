"""Character relation canvas: positions, manual edges, AI suggestions."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import character_relations
import gemini_client

ROOT = Path(__file__).resolve().parents[1]


class CharacterRelationUnitTests(unittest.TestCase):
    def test_extract_relations_prefers_heading(self) -> None:
        profile = "[외모]\n검은 머리\n\n[관계]\n엔케의 연인, 비비의 주인\n\n[성격]\n차분하다"
        self.assertEqual(
            character_relations.extract_relations_text(profile),
            "엔케의 연인, 비비의 주인",
        )

    def test_extract_relations_unstructured_profile(self) -> None:
        self.assertEqual(
            character_relations.extract_relations_text("엔케의 연인"),
            "엔케의 연인",
        )

    def test_prompt_includes_explicit_only_rules(self) -> None:
        system, user = character_relations.build_suggest_prompt(
            [{"id": 1, "name": "비비", "aliases": ["비"], "relations_text": "엔케의 연인"}]
        )
        blob = f"{system}\n{user}"
        self.assertIn("이름이 명시적으로 적힌 경우만", blob)
        self.assertIn("추론·정황·분위기만으로 짐작하지 마세요", blob)
        self.assertIn("사이가 나빠진 것 같았다", blob)
        self.assertIn("목록에 없는 이름은 만들지 마세요", blob)
        self.assertIn("외모·성격 비교", blob)
        self.assertIn("각각 별도 항목으로 넣으세요", blob)
        self.assertNotIn("한 쌍(두 인물)에는 라벨을 하나만 둡니다", blob)

    def test_filter_keeps_named_explicit_and_drops_guesses(self) -> None:
        roster = [
            {"id": 1, "name": "비비", "aliases": [], "relations_text": "엔케의 연인."},
            {"id": 2, "name": "엔케", "aliases": [], "relations_text": "비비의 주인."},
            {"id": 3, "name": "서윤", "aliases": [], "relations_text": "성격이 급하다."},
            {
                "id": 4,
                "name": "민재",
                "aliases": [],
                "relations_text": "서윤과 사이가 나빠진 것 같았다.",
            },
        ]
        raw = json.dumps({
            "relations": [
                {"a_id": 1, "b_id": 2, "label": "연인", "evidence": "엔케의 연인"},
                {"a_id": 1, "b_id": 3, "label": "가까운 사이", "evidence": "가까운 사이인 것 같다"},
                {"a_id": 3, "b_id": 4, "label": "소원해짐", "evidence": "사이가 나빠진 것 같았다"},
                {"a_id": 2, "b_id": 4, "label": "라이벌", "evidence": "둘은 라이벌로 보인다"},
            ]
        })
        parsed = character_relations.parse_relations_json(raw, roster)
        kept = character_relations.filter_suggestions(parsed, roster, set())
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["character_a_id"], 1)
        self.assertEqual(kept[0]["character_b_id"], 2)
        self.assertEqual(kept[0]["label"], "연인")

    def test_filter_skips_existing_pairs(self) -> None:
        roster = [
            {"id": 1, "name": "비비", "aliases": [], "relations_text": "엔케의 연인"},
            {"id": 2, "name": "엔케", "aliases": [], "relations_text": "비비의 주인"},
        ]
        parsed = [{
            "character_a_id": 1,
            "character_b_id": 2,
            "label": "연인",
            "evidence": "엔케의 연인",
        }]
        kept = character_relations.filter_suggestions(parsed, roster, {(1, 2, "연인")})
        self.assertEqual(kept, [])

    def test_parse_keeps_multiple_labels_for_same_pair(self) -> None:
        roster = [
            {"id": 1, "name": "비비", "aliases": [], "relations_text": "엔케의 연인"},
            {"id": 2, "name": "엔케", "aliases": [], "relations_text": "비비의 주인"},
        ]
        raw = json.dumps({
            "relations": [
                {"a_id": 1, "b_id": 2, "label": "연인", "evidence": "엔케의 연인"},
                {"a_id": 1, "b_id": 2, "label": "주인", "evidence": "비비의 주인"},
            ]
        })
        parsed = character_relations.parse_relations_json(raw, roster)
        kept = character_relations.filter_suggestions(parsed, roster, set())
        self.assertEqual({item["label"] for item in kept}, {"연인", "주인"})

    def test_filter_keeps_new_label_when_pair_already_confirmed(self) -> None:
        roster = [
            {"id": 1, "name": "비비", "aliases": [], "relations_text": "엔케의 연인. 엔케의 주인."},
            {"id": 2, "name": "엔케", "aliases": [], "relations_text": "비비의 주인"},
        ]
        parsed = [
            {"character_a_id": 1, "character_b_id": 2, "label": "연인", "evidence": "엔케의 연인"},
            {"character_a_id": 1, "character_b_id": 2, "label": "주인", "evidence": "비비의 주인"},
        ]
        kept = character_relations.filter_suggestions(parsed, roster, {(1, 2, "연인")})
        self.assertEqual([item["label"] for item in kept], ["주인"])

    def test_unnamed_counterpart_is_dropped(self) -> None:
        roster = [
            {"id": 1, "name": "비비", "aliases": [], "relations_text": "누군가의 연인"},
            {"id": 2, "name": "엔케", "aliases": [], "relations_text": ""},
        ]
        parsed = [{
            "character_a_id": 1,
            "character_b_id": 2,
            "label": "연인",
            "evidence": "누군가의 연인",
        }]
        kept = character_relations.filter_suggestions(parsed, roster, set())
        self.assertEqual(kept, [])


class CharacterRelationHttpTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=30
        )
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = raw
        return response.status, data

    def _project(self) -> int:
        status, project = self.request("POST", "/api/projects", {"title": "관계 작품", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def _character(self, project_id: int, name: str, profile: str, aliases: list[str] | None = None) -> int:
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/characters",
            {"name": name},
        )
        self.assertEqual(status, 201, created)
        cid = int(created["id"])
        status, detail = self.request("GET", f"/api/characters/{cid}")
        self.assertEqual(status, 200, detail)
        version = detail["character"]["row_version"]
        body = {
            "name": name,
            "profile_md": profile,
            "short_description": "",
            "row_version": version,
        }
        if aliases is not None:
            body["aliases"] = aliases
        status, _ = self.request("PUT", f"/api/characters/{cid}", body)
        self.assertEqual(status, 200)
        return cid

    def test_migration_75_on_init(self) -> None:
        with app.database() as connection:
            name = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 75"
            ).fetchone()
            self.assertEqual(name[0], "character_relations")
            name76 = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 76"
            ).fetchone()
            self.assertEqual(name76[0], "character_relations_label_unique")

    def test_position_persists_and_manual_edge_is_confirmed(self) -> None:
        pid = self._project()
        a = self._character(pid, "비비", "[관계]\n엔케의 연인")
        b = self._character(pid, "엔케", "[관계]\n비비의 주인")
        status, saved = self.request(
            "PUT",
            f"/api/projects/{pid}/character-canvas/positions",
            {"positions": [{"character_id": a, "x": 120, "y": 80}]},
        )
        self.assertEqual(status, 200, saved)
        status, created = self.request(
            "POST",
            f"/api/projects/{pid}/character-relations",
            {"character_a_id": b, "character_b_id": a, "label": "연인"},
        )
        self.assertEqual(status, 201, created)
        rel = created["relation"]
        self.assertEqual(rel["status"], "confirmed")
        self.assertEqual(rel["source"], "manual")
        self.assertEqual(rel["label"], "연인")
        self.assertEqual(min(rel["character_a_id"], rel["character_b_id"]), min(a, b))
        status, second = self.request(
            "POST",
            f"/api/projects/{pid}/character-relations",
            {"character_a_id": a, "character_b_id": b, "label": "주인"},
        )
        self.assertEqual(status, 201, second)
        self.assertEqual(second["relation"]["label"], "주인")
        status, canvas = self.request("GET", f"/api/projects/{pid}/character-canvas")
        self.assertEqual(status, 200, canvas)
        by_id = {int(item["id"]): item for item in canvas["characters"]}
        self.assertEqual(by_id[a]["x"], 120)
        self.assertEqual(by_id[a]["y"], 80)
        self.assertEqual({row["label"] for row in canvas["relations"]}, {"연인", "주인"})
        self.assertTrue(all(row["status"] == "confirmed" for row in canvas["relations"]))

    def test_ai_suggest_accept_reject_and_no_confirmed_duplicate(self) -> None:
        pid = self._project()
        a = self._character(pid, "비비", "[관계]\n엔케의 연인")
        b = self._character(pid, "엔케", "[관계]\n비비의 주인")
        c = self._character(pid, "서윤", "[관계]\n사이가 나빠진 것 같았다")
        d = self._character(pid, "민재", "성격이 급하다")
        fake = json.dumps({
            "relations": [
                {"a_id": a, "b_id": b, "label": "연인", "evidence": "엔케의 연인"},
                {"a_id": a, "b_id": b, "label": "주인", "evidence": "비비의 주인"},
                {"a_id": c, "b_id": d, "label": "소원해짐", "evidence": "사이가 나빠진 것 같았다"},
                {"a_id": b, "b_id": d, "label": "라이벌", "evidence": "둘은 라이벌로 보인다"},
            ]
        })
        original = gemini_client.generate_text
        gemini_client.generate_text = lambda *args, **kwargs: fake  # type: ignore[method-assign]
        try:
            with patch.object(gemini_client, "is_configured", return_value=True):
                status, result = self.request(
                    "POST", f"/api/projects/{pid}/character-relations/suggest"
                )
        finally:
            gemini_client.generate_text = original  # type: ignore[method-assign]
        self.assertEqual(status, 200, result)
        added = result["added"]
        self.assertEqual({row["label"] for row in added}, {"연인", "주인"}, result)
        lover = next(row for row in added if row["label"] == "연인")
        rel_id = int(lover["id"])
        status, accepted = self.request("POST", f"/api/character-relations/{rel_id}/accept")
        self.assertEqual(status, 200, accepted)
        self.assertEqual(accepted["relation"]["status"], "confirmed")
        gemini_client.generate_text = lambda *args, **kwargs: fake  # type: ignore[method-assign]
        try:
            with patch.object(gemini_client, "is_configured", return_value=True):
                status, again = self.request(
                    "POST", f"/api/projects/{pid}/character-relations/suggest"
                )
        finally:
            gemini_client.generate_text = original  # type: ignore[method-assign]
        self.assertEqual(status, 200, again)
        self.assertEqual(again["added"], [])
        canvas = again["canvas"]
        labels = {(row["label"], row["status"]) for row in canvas["relations"]}
        self.assertIn(("연인", "confirmed"), labels)
        self.assertIn(("주인", "suggested"), labels)

        status, extra = self.request(
            "POST",
            f"/api/projects/{pid}/character-relations",
            {"character_a_id": c, "character_b_id": d, "label": "라이벌"},
        )
        self.assertEqual(status, 201, extra)
        extra_id = int(extra["relation"]["id"])
        status, deleted = self.request("DELETE", f"/api/character-relations/{extra_id}")
        self.assertEqual(status, 200, deleted)
        status, canvas = self.request("GET", f"/api/projects/{pid}/character-canvas")
        labels = {row["label"] for row in canvas["relations"]}
        self.assertEqual(labels, {"연인", "주인"})

    def test_ui_has_relation_canvas(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="relationCanvas"', html)
        self.assertIn('id="openRelationCanvasButton"', html)
        self.assertIn('id="relationSuggestButton"', html)
        self.assertIn('id="relationFitButton"', html)
        self.assertIn('id="relationFullscreenExitButton"', html)
        self.assertIn("function openRelationCanvas", app_js)
        self.assertIn("function setRelationLabelAtLineMid", app_js)
        self.assertNotIn("placed.midY - 8", app_js)
        self.assertIn("function openDockRelationMinimapFloat", app_js)
        self.assertIn("function neighborhoodRelationData", app_js)
        self.assertIn("interactive: false", app_js)
        self.assertIn("showProfile: false", app_js)
        self.assertIn("function centerRelationCanvasOnCharacter", app_js)


if __name__ == "__main__":
    unittest.main()
