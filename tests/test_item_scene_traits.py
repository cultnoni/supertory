"""Scene-complete item detection (empty fill, pending badge, candidates)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import character_import_analysis
import gemini_client
import item_scene_traits


SCENE_TEXT = (
    "리아가 흑염검을 뽑아 들었다.\n"
    "흑염검의 칼날이 달빛을 먹었다.\n"
    "탁자 위에는 편지가 하나 놓여 있었다.\n"
    "별빛잔이 흔들렸다. 별빛잔이 김을 올렸다."
)


class ItemSceneTraitUnitTests(unittest.TestCase):
    def test_prompt_asks_for_repeated_proper_nouns_only(self) -> None:
        system, user = item_scene_traits.build_item_prompt(
            "리아가 흑염검을 뽑아 들었다.",
            [{"id": 1, "name": "흑염검", "aliases": ["흑검"], "description": ""}],
            ["흑염검", "흑검", "리아"],
        )
        blob = f"{system}\n{user}"
        self.assertIn("candidates", user)
        self.assertIn("한 번 스쳐 지나가는", blob)
        self.assertIn("일반명사", blob)
        self.assertIn("이미 있는 이름", blob)

    def test_parse_matches_registered_and_collects_candidates(self) -> None:
        mentioned = [{"id": 7, "name": "흑염검", "aliases": ["흑검"]}]
        raw = json.dumps({
            "items": [
                {"id": 7, "description": "달빛을 먹는 검"},
                {"name": "미등록", "description": "무시"},
            ],
            "candidates": ["별빛잔", "편지", "별빛잔"],
        })
        parsed, candidates = item_scene_traits.parse_item_analysis_json(raw, mentioned)
        self.assertEqual(parsed, [{"id": 7, "description": "달빛을 먹는 검"}])
        self.assertEqual(candidates, ["별빛잔", "편지"])

    def test_filter_requires_repeated_hits(self) -> None:
        kept = item_scene_traits.filter_repeated_candidates(
            SCENE_TEXT,
            ["별빛잔", "편지", "흑염검"],
            ["흑염검"],
        )
        self.assertEqual(kept, ["별빛잔"])


class ItemSceneTraitHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        self.original_gap = app.READER_DEBATE_GEMINI_GAP_SECONDS
        app.READER_DEBATE_GEMINI_GAP_SECONDS = 0
        app.initialise_database()
        app.reset_trait_analysis_state()
        app.reset_item_analysis_state()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        deadline = time.time() + 3
        while time.time() < deadline:
            with app._reader_comments_inflight_lock:
                comments_busy = bool(app._reader_comments_inflight)
            with app._trait_analysis_lock:
                traits_busy = bool(app._trait_analysis_inflight)
            with app._item_analysis_lock:
                items_busy = bool(app._item_analysis_inflight)
            if not comments_busy and not traits_busy and not items_busy:
                break
            time.sleep(0.05)
        app.reset_item_analysis_state()
        app.reset_trait_analysis_state()
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.READER_DEBATE_GEMINI_GAP_SECONDS = self.original_gap
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
        connection.request(
            method, path, body, {"Content-Type": "application/json"} if body else {}
        )
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _make_project_scene(self) -> tuple[int, int]:
        status, project = self.request(
            "POST", "/api/projects", {"title": "아이템 감지", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, project)
        status, chapter = self.request(
            "POST", f"/api/projects/{project['id']}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201, chapter)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201, scene)
        return int(project["id"]), int(scene["id"])

    def _save_scene(self, scene_id: int, *, status: str, content: str) -> dict:
        code, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(code, 200, detail)
        code, saved = self.request(
            "PUT",
            f"/api/scenes/{scene_id}",
            {
                "title": "1화",
                "status": status,
                "content_md": content,
                "row_version": detail.get("row_version") or 1,
            },
        )
        self.assertEqual(code, 200, saved)
        return saved if isinstance(saved, dict) else {}

    def _wait_item(self, scene_id: int) -> dict:
        for _ in range(50):
            code, job = self.request("GET", f"/api/scenes/{scene_id}/item-analysis")
            self.assertEqual(code, 200, job)
            status = str(job.get("status") or "idle")
            if status not in ("running", "idle"):
                return job
            time.sleep(0.1)
        self.fail("item analysis did not finish")

    def test_apply_fills_empty_pending_occupied_always_history(self) -> None:
        project_id, scene_id = self._make_project_scene()
        status, empty = self.request(
            "POST", f"/api/projects/{project_id}/items", {"name": "흑염검"}
        )
        self.assertEqual(status, 201, empty)
        status, occupied = self.request(
            "POST",
            f"/api/projects/{project_id}/items",
            {"name": "별빛잔", "description": "이미 적어 둔 잔"},
        )
        self.assertEqual(status, 201, occupied)
        with app.database() as connection:
            connection.execute(
                "INSERT INTO item_alias(item_id, project_id, alias, alias_type) "
                "VALUES (?, ?, '흑검', 'other')",
                (empty["id"], project_id),
            )
            mentioned = item_scene_traits.list_mentioned_items(
                connection, project_id, SCENE_TEXT
            )
            ids = {int(item["id"]) for item in mentioned}
            self.assertIn(int(empty["id"]), ids)
            self.assertIn(int(occupied["id"]), ids)
            summaries = item_scene_traits.apply_item_detections(
                connection,
                project_id=project_id,
                scene_id=scene_id,
                mentioned=mentioned,
                parsed=[
                    {"id": int(empty["id"]), "description": "달빛을 먹는 검"},
                    {"id": int(occupied["id"]), "description": "김이 오르는 잔"},
                ],
            )
        by_name = {item["name"]: item for item in summaries}
        self.assertEqual(by_name["흑염검"]["filled"], 1)
        self.assertEqual(by_name["별빛잔"]["pending"], 1)
        status, empty_detail = self.request("GET", f"/api/items/{empty['id']}")
        self.assertTrue(empty_detail["item"]["description"].startswith("〔토리〕"))
        status, occupied_detail = self.request("GET", f"/api/items/{occupied['id']}")
        self.assertEqual(occupied_detail["item"]["description"], "이미 적어 둔 잔")
        self.assertIn("description", occupied_detail["item"]["tori_analysis"])
        with app.database() as connection:
            history = connection.execute(
                "SELECT item_id, applied FROM item_trait_history WHERE scene_id = ? ORDER BY id",
                (scene_id,),
            ).fetchall()
        self.assertEqual(len(history), 2)
        applied_by = {int(row["item_id"]): int(row["applied"]) for row in history}
        self.assertEqual(applied_by[int(empty["id"])], 1)
        self.assertEqual(applied_by[int(occupied["id"])], 0)

    def test_apply_pending_description_endpoint(self) -> None:
        project_id, _scene_id = self._make_project_scene()
        status, item = self.request(
            "POST",
            f"/api/projects/{project_id}/items",
            {"name": "흑염검", "description": "기존 설명"},
        )
        self.assertEqual(status, 201, item)
        with app.database() as connection:
            connection.execute(
                "INSERT INTO item_tori_analysis(item_id, field_name, analyzed_content) "
                "VALUES (?, 'description', ?)",
                (item["id"], character_import_analysis.mark_tori_text("달빛을 먹는다")),
            )
        status, applied = self.request(
            "POST",
            f"/api/items/{item['id']}/tori-analysis/apply",
            {"field_name": "description"},
        )
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["item"]["description"].startswith("〔토리〕"))
        self.assertNotIn("description", applied["item"].get("tori_analysis") or {})

    @patch.object(gemini_client, "is_configured", return_value=True)
    def test_complete_schedules_fill_and_candidates(self, _configured) -> None:
        project_id, scene_id = self._make_project_scene()
        status, sword = self.request(
            "POST", f"/api/projects/{project_id}/items", {"name": "흑염검"}
        )
        self.assertEqual(status, 201, sword)
        payload = {
            "items": [{"id": int(sword["id"]), "description": "달빛을 먹는 검"}],
            "candidates": ["별빛잔", "편지"],
        }

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            blob = f"{system or ''}\n{prompt}"
            if "소품·아이템" in blob or "등록 아이템" in blob:
                return json.dumps(payload, ensure_ascii=False)
            return "댓글"

        with patch.object(gemini_client, "generate_text", side_effect=_fake):
            self._save_scene(scene_id, status="draft", content=SCENE_TEXT)
            self._save_scene(scene_id, status="complete", content=SCENE_TEXT)
            job = self._wait_item(scene_id)
        self.assertEqual(job["status"], "done")
        names = {item["name"]: item for item in job["items"]}
        self.assertIn("흑염검", names)
        self.assertEqual(job["candidates"], ["별빛잔"])
        status, detail = self.request("GET", f"/api/items/{sword['id']}")
        self.assertTrue(detail["item"]["description"].startswith("〔토리〕"))
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/items",
            {"name": "별빛잔"},
        )
        self.assertEqual(status, 201, created)
        status, listed = self.request("GET", f"/api/projects/{project_id}/items")
        self.assertEqual({row["name"] for row in listed}, {"흑염검", "별빛잔"})

    @patch.object(gemini_client, "is_configured", return_value=True)
    def test_complete_analysis_ignores_author_note_spoilers(self, _configured) -> None:
        project_id, scene_id = self._make_project_scene()
        status, sword = self.request(
            "POST", f"/api/projects/{project_id}/items", {"name": "흑염검"}
        )
        self.assertEqual(status, 201, sword)
        html = (
            "<p>리아가 흑염검을 뽑아 들었다.</p>"
            '<p data-author-note="1" class="st-author-note">'
            "// 이 검은 사실 저주받은 유물 CURSED_ITEM_SPOILER</p>"
            "<p>흑염검의 칼날이 달빛을 먹었다.</p>"
        )
        prompts: list[str] = []

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            blob = f"{system or ''}\n{prompt}"
            prompts.append(blob)
            if "소품·아이템" in blob or "등록 아이템" in blob:
                return json.dumps({
                    "items": [{"id": int(sword["id"]), "description": "달빛을 먹는 검"}],
                    "candidates": [],
                }, ensure_ascii=False)
            return "댓글"

        with patch.object(gemini_client, "generate_text", side_effect=_fake):
            self._save_scene(scene_id, status="draft", content=html)
            self._save_scene(scene_id, status="complete", content=html)
            job = self._wait_item(scene_id)
        self.assertEqual(job["status"], "done", job)
        item_blobs = [blob for blob in prompts if "소품·아이템" in blob or "등록 아이템" in blob]
        self.assertTrue(item_blobs)
        joined = "\n".join(item_blobs)
        self.assertNotIn("CURSED_ITEM_SPOILER", joined)
        self.assertNotIn("저주받은 유물", joined)
        self.assertIn("흑염검", joined)
        self.assertIn("달빛을 먹었다", joined)
        status, detail = self.request("GET", f"/api/items/{sword['id']}")
        description = str(detail["item"].get("description") or "")
        self.assertNotIn("CURSED_ITEM_SPOILER", description)
        self.assertNotIn("저주받은", description)

    @patch.object(gemini_client, "is_configured", return_value=False)
    def test_complete_skips_without_gemini(self, _configured) -> None:
        project_id, scene_id = self._make_project_scene()
        status, sword = self.request(
            "POST", f"/api/projects/{project_id}/items", {"name": "흑염검"}
        )
        self.assertEqual(status, 201, sword)
        self._save_scene(scene_id, status="draft", content=SCENE_TEXT)
        self._save_scene(scene_id, status="complete", content=SCENE_TEXT)
        job = self._wait_item(scene_id)
        self.assertEqual(job["status"], "skipped")
        self.assertEqual(job.get("error"), "gemini_not_configured")
        status, detail = self.request("GET", f"/api/items/{sword['id']}")
        self.assertEqual(detail["item"]["description"], "")

    def test_item_trait_history_api_binder_order(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "아이템 연대기", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, project)
        pid = int(project["id"])
        status, ch_a = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "먼저 폴더"})
        self.assertEqual(status, 201, ch_a)
        status, ch_b = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "나중 폴더"})
        self.assertEqual(status, 201, ch_b)
        status, scene_a = self.request(
            "POST", f"/api/chapters/{ch_a['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201, scene_a)
        status, scene_b = self.request(
            "POST", f"/api/chapters/{ch_b['id']}/scenes", {"title": "2화"}
        )
        self.assertEqual(status, 201, scene_b)
        status, item = self.request(
            "POST", f"/api/projects/{pid}/items", {"name": "흑염검"}
        )
        self.assertEqual(status, 201, item)
        item_id = int(item["id"])
        with app.database() as connection:
            connection.execute(
                "UPDATE chapter SET sort_order = 99 WHERE id = ?", (ch_a["id"],)
            )
            connection.execute(
                "UPDATE chapter SET sort_order = 0 WHERE id = ?", (ch_b["id"],)
            )
            connection.execute(
                "UPDATE chapter SET sort_order = 1 WHERE id = ?", (ch_a["id"],)
            )
            mentioned = [{"id": item_id, "name": "흑염검", "description": ""}]
            item_scene_traits.apply_item_detections(
                connection,
                project_id=pid,
                scene_id=int(scene_b["id"]),
                mentioned=mentioned,
                parsed=[{"id": item_id, "description": "달빛을 먹는다"}],
            )
            mentioned[0]["description"] = character_import_analysis.mark_tori_text(
                "달빛을 먹는다"
            )
            item_scene_traits.apply_item_detections(
                connection,
                project_id=pid,
                scene_id=int(scene_a["id"]),
                mentioned=mentioned,
                parsed=[{"id": item_id, "description": "검은 칼날"}],
            )
        status, payload = self.request("GET", f"/api/items/{item_id}/trait-history")
        self.assertEqual(status, 200, payload)
        entries = payload.get("entries") or []
        self.assertEqual(len(entries), 2)
        self.assertEqual(int(entries[0]["scene_id"]), int(scene_a["id"]))
        self.assertEqual(int(entries[1]["scene_id"]), int(scene_b["id"]))
        self.assertFalse(entries[0]["applied"])
        self.assertTrue(entries[1]["applied"])


if __name__ == "__main__":
    unittest.main()
