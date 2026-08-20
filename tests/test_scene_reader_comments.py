"""Auto-generated virtual-reader comments for completed scenes."""

from __future__ import annotations

import http.client
import json
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app
import gemini_client


class SceneReaderCommentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        self.original_generate = gemini_client.generate_text
        self.original_gap = app.READER_DEBATE_GEMINI_GAP_SECONDS
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.READER_DEBATE_GEMINI_GAP_SECONDS = 0
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.calls: list[dict] = []

        def _fake_generate(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system or ""})
            name = ""
            system_text = system or ""
            for line in system_text.splitlines():
                if "당신은 '" in line and "가상 독자" in line:
                    name = line
                    break
            return f"댓글:{len(self.calls)}:{name[:40]}"

        gemini_client.generate_text = _fake_generate  # type: ignore[method-assign]

    def tearDown(self) -> None:
        gemini_client.generate_text = self.original_generate  # type: ignore[method-assign]
        app.READER_DEBATE_GEMINI_GAP_SECONDS = self.original_gap
        with app._reader_comments_inflight_lock:
            app._reader_comments_inflight.clear()
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, object]:
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

    def _make_scene(self, *, main_genre: str = "romfant", sub_genre: str = "high") -> int:
        status, project = self.request(
            "POST",
            "/api/projects",
            {
                "title": "댓글 테스트",
                "main_genre": main_genre,
                "sub_genre": sub_genre,
            },
        )
        self.assertEqual(status, 201, project)
        status, chapter = self.request(
            "POST",
            f"/api/projects/{project['id']}/chapters",
            {"title": "장"},
        )
        self.assertEqual(status, 201, chapter)
        status, scene = self.request(
            "POST",
            f"/api/chapters/{chapter['id']}/scenes",
            {"title": "완성 회차"},
        )
        self.assertEqual(status, 201, scene)
        scene_id = int(scene["id"])
        status, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200, detail)
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene_id}",
            {
                "title": "완성 회차",
                "status": "draft",
                "content_md": "황제가 무릎을 꿇고 사죄했다.",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200, saved)
        return scene_id

    def _wait_comments(self, scene_id: int, *, minimum: int = 1, timeout: float = 8.0) -> dict:
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            status, data = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
            self.assertEqual(status, 200, data)
            last = data if isinstance(data, dict) else {}
            comments = last.get("comments") or []
            if not last.get("generating") and len(comments) >= minimum:
                return last
            time.sleep(0.05)
        self.fail(f"댓글이 채워지지 않았습니다: {last}")
        return last

    def test_select_prefers_genre_strings_in_name(self) -> None:
        with app.database() as connection:
            picked = app.select_reader_comment_personas(
                "romfant",
                "sf",
                connection=connection,
                rng=random.Random(7),
            )
        self.assertEqual(len(picked), 3)
        ids = [str(item["id"]) for item in picked]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(picked[0]["category"], "genre_specialist")
        self.assertIn("로판", f"{picked[0]['name']}{picked[0]['identity']}")
        self.assertEqual(picked[1]["category"], "sub_genre_specialist")
        self.assertIn("SF", f"{picked[1]['name']}{picked[1]['identity']}".upper())
        self.assertIn(
            picked[2]["category"],
            {"narrative_critic", "taste_preference", "structure_wildcard"},
        )

    def test_select_falls_back_when_genre_does_not_match(self) -> None:
        with app.database() as connection:
            picked = app.select_reader_comment_personas(
                "zzzz_unknown",
                "yyyy_unknown",
                connection=connection,
                rng=random.Random(3),
            )
        self.assertEqual(len(picked), 3)
        self.assertEqual(picked[0]["category"], "genre_specialist")
        self.assertEqual(picked[1]["category"], "sub_genre_specialist")

    def test_generate_persists_each_comment_and_skips_failures(self) -> None:
        scene_id = self._make_scene()
        original = gemini_client.generate_text
        calls = {"n": 0}

        def _flaky(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            calls["n"] += 1
            if calls["n"] == 2:
                raise gemini_client.GeminiError("중간 실패")
            return f"살아남은 댓글 {calls['n']}"

        gemini_client.generate_text = _flaky  # type: ignore[method-assign]
        try:
            app.generate_scene_reader_comments(scene_id)
        finally:
            gemini_client.generate_text = original  # type: ignore[method-assign]
        status, data = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, data)
        comments = data["comments"]
        self.assertEqual(len(comments), 2)
        self.assertEqual(data["expected"], 3)
        self.assertFalse(data["generating"])
        self.assertTrue(all(item.get("avatar_url", "").startswith("/assets/reader_avatars/") for item in comments))
        self.assertTrue(all(item.get("persona_name") for item in comments))

    def test_post_generate_is_idempotent_and_save_complete_starts_it(self) -> None:
        scene_id = self._make_scene()
        status, first = self.request("POST", f"/api/scenes/{scene_id}/reader-comments/generate")
        self.assertIn(status, (200, 202), first)
        ready = self._wait_comments(scene_id, minimum=3)
        self.assertEqual(len(ready["comments"]), 3)
        first_ids = [item["id"] for item in ready["comments"]]

        status, again = self.request("POST", f"/api/scenes/{scene_id}/reader-comments/generate")
        self.assertEqual(status, 200, again)
        self.assertEqual([item["id"] for item in again["comments"]], first_ids)

        status, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200, detail)
        before_count = len(self.calls)
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene_id}",
            {
                "title": "완성 회차",
                "status": "complete",
                "content_md": "황제가 무릎을 꿇고 사죄했다.",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200, saved)
        time.sleep(0.2)
        status, after = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, after)
        self.assertEqual(len(after["comments"]), 3)
        self.assertEqual(len(self.calls), before_count)

    def test_save_scene_complete_triggers_generation(self) -> None:
        scene_id = self._make_scene()
        status, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200, detail)
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene_id}",
            {
                "title": "완성 회차",
                "status": "complete",
                "content_md": "황제가 무릎을 꿇고 사죄했다.",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200, saved)
        ready = self._wait_comments(scene_id, minimum=3)
        self.assertEqual(len(ready["comments"]), 3)
        self.assertGreaterEqual(len(self.calls), 3)
        self.assertTrue(any("완성된 회차" in (call["system"] or "") for call in self.calls))
        self.assertTrue(any("[작품 정보]" in (call["system"] or "") for call in self.calls))
