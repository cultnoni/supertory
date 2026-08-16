"""Tracked facts: schema, prompt contracts, persistence, optional Gemini smoke."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


JS_PATH = Path(__file__).resolve().parents[1] / "web" / "app.js"


class TrackedFactsContractTests(unittest.TestCase):
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

    def create_project_with_scene(self, title: str = "추적사실") -> tuple[int, int]:
        status, project = self.request(
            "POST", "/api/projects", {"title": title, "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, project)
        status, chapter = self.request(
            "POST", f"/api/projects/{project['id']}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201)
        return int(project["id"]), int(scene["id"])

    def test_migration_applied_on_init(self) -> None:
        with app.database() as connection:
            scene_cols = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(scene_summary)")
            }
            index_cols = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(project_index)")
            }
            version = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 48"
            ).fetchone()
        self.assertIn("tracked_facts_json", scene_cols)
        self.assertIn("tracked_facts_json", index_cols)
        self.assertEqual(version[0], "tracked_facts")

    def test_scene_summary_prompt_asks_for_tracked_facts(self) -> None:
        prompt = app.SuperToryHandler.build_scene_summary_prompt(
            "한서는 왼팔을 다쳤다.", "(누적 맥락 없음)"
        )
        self.assertIn('"tracked_facts"', prompt)
        self.assertIn("신체상태", prompt)
        self.assertIn("소지품", prompt)
        self.assertIn("scene_ref", prompt)
        self.assertIn("명확히 드러난 것만", prompt)
        js = JS_PATH.read_text(encoding="utf-8")
        self.assertIn('"tracked_facts": []', js)
        self.assertIn("애매하거나 확신이 없는 사실은 넣지 않습니다", js)

    def test_index_merge_prompt_keeps_history(self) -> None:
        prompt = app.SuperToryHandler.build_index_merge_prompt(
            {"characters": [], "tracked_facts": []},
            [{"event_summary": "부상", "tracked_facts": []}],
        )
        self.assertIn("since_scene", prompt)
        self.assertIn("history", prompt)
        self.assertIn("같은 subject + attribute", prompt)
        self.assertIn("압축·요약하지 않는다", prompt)
        self.assertIn("resolved_threads에 해당하는 항목은 open_threads에서 제거한다", prompt)
        js = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("새로 들어온 tracked_facts를 기존 project_index의 tracked_facts와 병합하세요", js)

    def test_setting_break_prompt_priority_2_1(self) -> None:
        prompt = app.SuperToryHandler._build_setting_break_scan_prompt("원고")
        self.assertIn("2-1.", prompt)
        self.assertIn("tracked_facts", prompt)
        self.assertIn("오른팔 부상", prompt)
        self.assertIn("[세계관 검사 기준]", prompt)
        self.assertIn("[캐릭터 일관성 검사 기준]", prompt)
        self.assertIn("[출력 형식]", prompt)
        self.assertNotIn("Core Identity", prompt)
        multi = app.SuperToryHandler._build_setting_break_scan_multi_prompt("### 1화\n본문")
        self.assertIn("2-1.", multi)
        js = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("2-1. [프로젝트 누적 정보]의 tracked_facts", js)

    def test_upsert_persists_tracked_facts_column(self) -> None:
        project_id, scene_id = self.create_project_with_scene()
        fact = {
            "category": "신체상태",
            "subject": "한서",
            "attribute": "왼팔",
            "value": "부상",
            "scene_ref": "왼팔을 다쳤다",
        }
        status, result = self.request(
            "PUT",
            f"/api/scenes/{scene_id}/summary",
            {
                "summary": {
                    "event_summary": "한서가 왼팔을 다쳤다",
                    "characters_involved": ["한서"],
                    "new_world_facts": [],
                    "new_threads": [],
                    "resolved_threads": [],
                    "tracked_facts": [fact],
                }
            },
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result.get("tracked_facts")[0]["attribute"], "왼팔")

        status, got = self.request("GET", f"/api/scenes/{scene_id}/summary")
        self.assertEqual(status, 200)
        self.assertEqual(got["tracked_facts"][0]["subject"], "한서")
        self.assertEqual(got["summary"]["tracked_facts"][0]["value"], "부상")

        with app.database() as connection:
            raw = connection.execute(
                "SELECT tracked_facts_json FROM scene_summary WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()[0]
        self.assertIn("왼팔", raw)

        status, index = self.request("GET", f"/api/projects/{project_id}/index")
        self.assertEqual(status, 200)
        self.assertIn("tracked_facts", index)
        self.assertIn("추적 대상 사실", index.get("previous_context") or "")

    def test_js_injects_tracked_facts_into_index_context(self) -> None:
        js = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("function formatProjectIndexContext", js)
        self.assertIn("추적 대상 사실:", js)
        self.assertIn("tracked_facts: Array.isArray(projectIndex.tracked_facts)", js)


@unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
class TrackedFactsLiveGeminiTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_extract_and_detect_injury_contradiction(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "부상 추적", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        status, chapter = self.request(
            "POST", f"/api/projects/{project['id']}/chapters", {"title": "장"}
        )
        self.assertEqual(status, 201)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201)
        scene_id = scene["id"]
        status, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200)
        injury_text = (
            "한서는 칼날을 막으려다 왼팔을 다쳤다. "
            "피가 흘렀고 그는 왼팔을 감싸 쥐며 무릎을 꿇었다. "
            "주변 병사들이 그를 부축해 전장에서 물러나게 했다."
        )
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene_id}",
            {
                "title": "1화",
                "content_md": injury_text,
                "status": "draft",
                "row_version": detail.get("row_version") or 1,
            },
        )
        self.assertEqual(status, 200, saved)

        prompt = app.SuperToryHandler.build_scene_summary_prompt(injury_text, "(누적 맥락 없음)")
        status, summarized = self.request(
            "POST",
            f"/api/scenes/{scene_id}/summarize",
            {"prompt": prompt, "content_md": injury_text},
        )
        self.assertEqual(status, 200, summarized)
        facts = summarized.get("tracked_facts") or summarized.get("summary", {}).get("tracked_facts") or []
        blob = json.dumps(facts, ensure_ascii=False)
        self.assertTrue(facts, f"tracked_facts empty: {summarized.get('summary')}")
        self.assertIn("신체상태", blob)
        self.assertTrue(
            ("왼팔" in blob) or ("팔" in blob),
            f"expected left-arm injury fact:\n{blob}",
        )
        self.assertTrue(
            ("부상" in blob) or ("다쳤" in blob) or ("상처" in blob),
            f"expected injury value:\n{blob}",
        )

        status, merged = self.request(
            "POST",
            f"/api/projects/{project['id']}/index/merge",
            {"only_if_dirty": True},
        )
        self.assertEqual(status, 200, merged)
        index_facts = (merged.get("index") or {}).get("tracked_facts") or []
        if not index_facts:
            status, index = self.request("GET", f"/api/projects/{project['id']}/index")
            index_facts = index.get("tracked_facts") or []
        self.assertTrue(index_facts, f"merged tracked_facts empty: {merged}")

        contradiction = (
            "한서는 왼손으로 검을 힘껏 휘둘렀다. "
            "칼끝이 허공을 가르고 상대의 방패를 두 동강 냈다."
        )
        indexed = (
            "[프로젝트 누적 정보 - 참고용]\n"
            '등장인물: ["한서"]\n'
            "세계관 설정: 중세 검술 세계\n"
            "지금까지 줄거리: 한서가 전투 중 왼팔을 다쳤다\n"
            "미회수 복선: \n"
            f"추적 대상 사실: {json.dumps(index_facts, ensure_ascii=False)}\n\n"
            + app.SuperToryHandler._build_setting_break_scan_prompt(contradiction)
        )
        status, flagged = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worldscan",
                "project_id": project["id"],
                "project_title": "부상 추적",
                "scene_title": "2화",
                "scene_content": contradiction,
                "indexed_prompt": indexed,
            },
        )
        self.assertEqual(status, 200, flagged)
        out_flag = str(flagged.get("text") or "")
        self.assertNotIn("발견되지 않았습니다", out_flag)
        self.assertTrue(
            ("왼손" in out_flag)
            or ("왼팔" in out_flag)
            or ("부상" in out_flag)
            or ("모순" in out_flag)
            or ("어긋" in out_flag),
            f"contradiction should be flagged:\n{out_flag}",
        )

        consistent = (
            "한서는 다친 왼팔을 가슴에 붙인 채 오른손으로 검을 들어 올렸다. "
            "통증 때문에 왼손은 쓰지 못했다."
        )
        indexed_ok = (
            "[프로젝트 누적 정보 - 참고용]\n"
            '등장인물: ["한서"]\n'
            "세계관 설정: 중세 검술 세계\n"
            "지금까지 줄거리: 한서가 전투 중 왼팔을 다쳤다\n"
            "미회수 복선: \n"
            f"추적 대상 사실: {json.dumps(index_facts, ensure_ascii=False)}\n\n"
            + app.SuperToryHandler._build_setting_break_scan_prompt(consistent)
        )
        status, clean = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worldscan",
                "project_id": project["id"],
                "project_title": "부상 추적",
                "scene_title": "2화",
                "scene_content": consistent,
                "indexed_prompt": indexed_ok,
            },
        )
        self.assertEqual(status, 200, clean)
        out_clean = str(clean.get("text") or "")
        self.assertTrue(
            ("발견되지 않았습니다" in out_clean)
            or out_clean.count("유형:") == 0,
            f"consistent manuscript should not over-flag:\n{out_clean}",
        )
        print("\n=== EXTRACTED FACTS ===\n", json.dumps(facts, ensure_ascii=False, indent=2))
        print("\n=== CONTRADICTION SCAN ===\n", out_flag)
        print("\n=== CONSISTENT SCAN ===\n", out_clean)


if __name__ == "__main__":
    unittest.main()
