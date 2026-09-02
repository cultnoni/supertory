"""Auto-generated virtual-reader comments for completed scenes."""

from __future__ import annotations

import http.client
import io
import json
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

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
            app._reader_comments_inflight_batch.clear()
            app._reader_comments_last_error.clear()
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
        self.assertEqual(data.get("last_error_code"), "unknown")
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
        self.assertEqual(ready["refresh_count"], 0)
        self.assertEqual(ready["max_refreshes"], 3)
        self.assertTrue(ready["can_refresh"])

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
        time.sleep(0.2)
        status, after = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, after)
        self.assertFalse(after.get("generating"), after)
        self.assertEqual(len(after["comments"]), 3)
        self.assertEqual(len(after["batches"]), 1)
        self.assertEqual([item["id"] for item in after["comments"]], first_ids)
        self.assertTrue(after["can_refresh"])

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
        self.assertTrue(
            any("웹소설 플랫폼 댓글창" in (call["system"] or "") for call in self.calls)
        )
        self.assertTrue(any("[작품 정보]" in (call["system"] or "") for call in self.calls))
        self.assertTrue(
            any("웹소설 플랫폼 댓글창" in (call["prompt"] or "") for call in self.calls)
        )

    def test_comment_prompt_is_internet_comment_style_not_review(self) -> None:
        with app.database() as connection:
            persona = dict(
                connection.execute(
                    "SELECT * FROM virtual_reader_personas WHERE id = ?",
                    ("roppan_cider",),
                ).fetchone()
            )
        shared = "[프로젝트 누적 정보]\n[아직 안 풀린 떡밥/복선 목록]\n- 열쇠"
        system = app._reader_comment_system_prompt(persona, shared)
        self.assertIn("웹소설 플랫폼 댓글창에 이 독자가 남길 법한 댓글", system)
        self.assertIn("이 회차를 평가하지 마세요", system)
        self.assertIn("1~2문장", system)
        self.assertIn("존댓말/정중한 피드백 톤 금지", system)
        self.assertIn("좋았어요", system)
        self.assertIn("잘 읽었습니다", system)
        self.assertIn("특정 장면·대사·행동", system)
        self.assertIn("[말투]", system)
        self.assertIn("[말투 예시]", system)
        self.assertIn("말투는 페르소나마다 다름", system)
        self.assertIn("팩폭하듯 단정적으로", system)
        self.assertNotIn("톤은 무조건 캐주얼한 인터넷 댓글체로 통일", system)
        self.assertIn("[프로젝트 누적 정보]", system)
        self.assertIn("ㅋㅋ 3화에 나왔던 열쇠 복선 여기서 회수하네 미쳤다", system)
        self.assertIn("남주 인성 실화냐? 여기서 여주 버리면 진짜 이탈함", system)
        self.assertIn("다음 화에 주인공 흑화할 듯? 떡밥 정리해 둠", system)
        self.assertIn("이 독자의 [말투]에 가까운 결만 참고", system)
        self.assertNotIn("2~5문장", system)
        self.assertNotIn("장르 불일치 인지", system)
        user = app._reader_comment_user_prompt("사이다 중독자", "완성 회차")
        self.assertIn("웹소설 플랫폼 댓글창에 실제 독자가 남길 법한 댓글", user)
        self.assertIn("사이다 중독자", user)
        self.assertIn("완성 회차", user)
        self.assertIn("평가하지 말고", user)

    def test_generate_includes_project_index_in_system_prompt(self) -> None:
        scene_id = self._make_scene()
        with app.database() as connection:
            work_id = connection.execute(
                "SELECT project_id FROM scene WHERE id = ?",
                (scene_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO project_index("
                "project_id, characters_json, world_rules_json, timeline_json, "
                "open_threads_json, tracked_facts_json, index_dirty, pending_scene_ids_json"
                ") VALUES (?, ?, ?, ?, ?, ?, 0, '[]') "
                "ON CONFLICT(project_id) DO UPDATE SET "
                "characters_json=excluded.characters_json, "
                "world_rules_json=excluded.world_rules_json, "
                "timeline_json=excluded.timeline_json, "
                "open_threads_json=excluded.open_threads_json, "
                "tracked_facts_json=excluded.tracked_facts_json",
                (
                    work_id,
                    '["황제"]',
                    "[]",
                    '["3화: 금고에서 낡은 열쇠를 줍는다."]',
                    '["열쇠가 무엇을 여는지"]',
                    '[{"category":"소품","subject":"열쇠","attribute":"출처","value":"3화 금고","since_scene":"3"}]',
                ),
            )
        before = len(self.calls)
        app.generate_scene_reader_comments(scene_id)
        systems = "\n".join(call["system"] for call in self.calls[before:])
        self.assertIn("[프로젝트 누적 정보]", systems)
        self.assertIn("[지금까지 전개 - 실제 원고 문장 아님, 요약임]", systems)
        self.assertIn("금고에서 낡은 열쇠를 줍는다", systems)
        self.assertIn("[아직 안 풀린 떡밥/복선 목록]", systems)
        self.assertIn("열쇠가 무엇을 여는지", systems)
        self.assertIn("[추적 중인 설정/인물 사실]", systems)
        self.assertIn("3화 금고", systems)

    def _persona_ids(self) -> list[str]:
        with app.database() as connection:
            rows = connection.execute(
                "SELECT id FROM virtual_reader_personas ORDER BY display_order, id"
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def _insert_comments(
        self, scene_id: int, persona_ids: list[str], batch_id: str = ""
    ) -> None:
        stamp = batch_id or app.utc_timestamp_now()
        with app.database() as connection:
            for persona_id in persona_ids:
                connection.execute(
                    """
                    INSERT INTO scene_reader_comments
                        (scene_id, batch_id, persona_id, comment_text, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        scene_id,
                        batch_id,
                        persona_id,
                        f"댓글 {persona_id}",
                        stamp,
                    ),
                )

    def test_select_excludes_existing_persona_ids(self) -> None:
        with app.database() as connection:
            first = app.select_reader_comment_personas(
                "romfant",
                "sf",
                connection=connection,
                rng=random.Random(7),
            )
            first_ids = [str(item["id"]) for item in first]
            second = app.select_reader_comment_personas(
                "romfant",
                "sf",
                connection=connection,
                rng=random.Random(7),
                exclude_persona_ids=first_ids,
            )
        second_ids = [str(item["id"]) for item in second]
        self.assertEqual(len(second), 3)
        self.assertTrue(set(first_ids).isdisjoint(second_ids))

    def test_select_additional_returns_fewer_when_pool_is_small(self) -> None:
        ids = self._persona_ids()
        keep = ids[-2:]
        exclude = ids[:-2]
        with app.database() as connection:
            picked = app.select_additional_reader_comment_personas(
                "romfant",
                "sf",
                exclude_persona_ids=exclude,
                connection=connection,
                rng=random.Random(1),
            )
        self.assertEqual(len(picked), 2)
        self.assertEqual({item["id"] for item in picked}, set(keep))

    def test_select_additional_injects_joker_about_a_quarter_of_the_time(self) -> None:
        original_chance = app.READER_COMMENT_JOKER_CHANCE
        try:
            with app.database() as connection:
                app.READER_COMMENT_JOKER_CHANCE = 0
                baseline = app.select_additional_reader_comment_personas(
                    "romfant",
                    "sf",
                    connection=connection,
                    rng=random.Random(7),
                )
                app.READER_COMMENT_JOKER_CHANCE = 1
                jokered = app.select_additional_reader_comment_personas(
                    "romfant",
                    "sf",
                    connection=connection,
                    rng=random.Random(7),
                )
        finally:
            app.READER_COMMENT_JOKER_CHANCE = original_chance
        base_ids = {str(item["id"]) for item in baseline}
        joker_ids = {str(item["id"]) for item in jokered}
        self.assertEqual(len(baseline), 3)
        self.assertEqual(len(jokered), 3)
        self.assertNotEqual(base_ids, joker_ids)
        swapped = joker_ids - base_ids
        self.assertEqual(len(swapped), 1)
        swapped_id = next(iter(swapped))
        swapped_persona = next(item for item in jokered if item["id"] == swapped_id)
        self.assertIn(swapped_persona["category"], app.READER_COMMENT_WILDCARD_CATEGORIES)

    def test_get_comments_includes_refresh_fields(self) -> None:
        scene_id = self._make_scene()
        status, empty = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, empty)
        self.assertEqual(empty["refresh_count"], 0)
        self.assertEqual(empty["max_refreshes"], 3)
        self.assertFalse(empty["can_refresh"])
        self.assertFalse(empty["has_history"])
        self.assertEqual(empty["batches"], [])
        self.assertIsNone(empty.get("last_error_code"))

    def test_post_generate_starts_a_new_batch_and_keeps_previous(self) -> None:
        scene_id = self._make_scene()
        status, first = self.request("POST", f"/api/scenes/{scene_id}/reader-comments/generate")
        self.assertIn(status, (200, 202), first)
        ready = self._wait_comments(scene_id, minimum=3)
        first_ids = {item["id"] for item in ready["comments"]}
        first_batch = ready["batches"][0]["batch_id"]
        self.assertEqual(len(ready["batches"]), 1)
        self.assertTrue(ready["has_history"])
        status, refresh = self.request("POST", f"/api/scenes/{scene_id}/reader-comments/generate")
        self.assertEqual(status, 202, refresh)
        self.assertTrue(refresh.get("started") or refresh.get("generating"))
        more = self._wait_comments(scene_id, minimum=6)
        self.assertEqual(len(more["comments"]), 6)
        self.assertEqual(len(more["batches"]), 2)
        self.assertEqual(more["refresh_count"], 1)
        self.assertTrue(more["can_refresh"])
        self.assertTrue(first_ids.issubset({item["id"] for item in more["comments"]}))
        batch_ids = [item["batch_id"] for item in more["batches"]]
        self.assertEqual(len(set(batch_ids)), 2)
        self.assertIn(first_batch, batch_ids)
        self.assertNotEqual(more["batches"][0]["created_at"], more["batches"][1]["created_at"])

    def test_legacy_comments_without_batch_id_form_one_history_group(self) -> None:
        scene_id = self._make_scene()
        used = self._persona_ids()[:3]
        self._insert_comments(scene_id, used, batch_id="")
        status, data = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data["comments"]), 3)
        self.assertEqual(len(data["batches"]), 1)
        self.assertTrue(data["has_history"])
        self.assertEqual(len(data["batches"][0]["comments"]), 3)

    def test_refresh_returns_no_more_personas_when_pool_empty(self) -> None:
        scene_id = self._make_scene()
        self._insert_comments(scene_id, self._persona_ids()[:3], batch_id="legacy-one")
        original = app.select_reader_comment_personas
        app.select_reader_comment_personas = (  # type: ignore[method-assign]
            lambda *args, **kwargs: []
        )
        try:
            status, data = self.request(
                "POST", f"/api/scenes/{scene_id}/reader-comments/generate"
            )
        finally:
            app.select_reader_comment_personas = original  # type: ignore[method-assign]
        self.assertEqual(status, 200, data)
        self.assertFalse(data.get("started"))
        self.assertEqual(len(data["comments"]), 3)

    def test_post_generate_does_not_fill_previous_batch(self) -> None:
        scene_id = self._make_scene()
        used = self._persona_ids()[:2]
        self._insert_comments(scene_id, used, batch_id="2026-08-15T00:00:00.000000Z")
        status, before = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, before)
        self.assertEqual(len(before["comments"]), 2)
        self.assertEqual(len(before["batches"]), 1)
        status, fill = self.request("POST", f"/api/scenes/{scene_id}/reader-comments/generate")
        self.assertEqual(status, 202, fill)
        self.assertTrue(fill.get("started") or fill.get("generating"))
        ready = self._wait_comments(scene_id, minimum=5)
        self.assertEqual(len(ready["comments"]), 5)
        self.assertEqual(len(ready["batches"]), 2)
        kept = {item["id"] for item in before["comments"]}
        self.assertTrue(kept.issubset({item["id"] for item in ready["comments"]}))
        new_batch = next(
            item
            for item in ready["batches"]
            if item["batch_id"] != "2026-08-15T00:00:00.000000Z"
        )
        self.assertEqual(len(new_batch["comments"]), 3)

    def test_generate_logs_gemini_failures_including_quota(self) -> None:
        scene_id = self._make_scene()
        previous = gemini_client.generate_text

        def _fail(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system or ""})
            raise gemini_client.GeminiError(
                "429 RESOURCE_EXHAUSTED quota exceeded",
                code="quota",
                http_status=429,
            )

        gemini_client.generate_text = _fail  # type: ignore[method-assign]
        buf = io.StringIO()
        try:
            with mock.patch("sys.stdout", buf):
                app.generate_scene_reader_comments(scene_id)
        finally:
            gemini_client.generate_text = previous  # type: ignore[method-assign]
        text = buf.getvalue()
        self.assertIn("가상독자 댓글 생성 실패:", text)
        self.assertIn(f"scene={scene_id}", text)
        self.assertIn("persona=", text)
        self.assertIn("일일 할당량 소진", text)
        self.assertEqual(len(self.calls), 1)
        status, data = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["comments"], [])
        self.assertEqual(data.get("last_error_code"), "quota")
        self.assertFalse(data["can_refresh"])

    def test_rate_limit_tries_all_personas_and_stays_retryable(self) -> None:
        scene_id = self._make_scene()
        previous = gemini_client.generate_text
        calls = {"n": 0}

        def _fail(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            calls["n"] += 1
            raise gemini_client.GeminiError(
                "429 RESOURCE_EXHAUSTED",
                code="rate_limit",
                http_status=429,
            )

        gemini_client.generate_text = _fail  # type: ignore[method-assign]
        try:
            app.generate_scene_reader_comments(scene_id)
        finally:
            gemini_client.generate_text = previous  # type: ignore[method-assign]
        self.assertEqual(calls["n"], 3)
        status, data = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["comments"], [])
        self.assertEqual(data.get("last_error_code"), "rate_limit")

    def test_successful_generate_clears_quota_last_error(self) -> None:
        scene_id = self._make_scene()
        previous = gemini_client.generate_text

        def _fail(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            raise gemini_client.GeminiError("daily", code="quota", http_status=429)

        gemini_client.generate_text = _fail  # type: ignore[method-assign]
        try:
            app.generate_scene_reader_comments(scene_id)
        finally:
            gemini_client.generate_text = previous  # type: ignore[method-assign]
        status, failed = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, failed)
        self.assertEqual(failed.get("last_error_code"), "quota")
        app.generate_scene_reader_comments(scene_id)
        status, data = self.request("GET", f"/api/scenes/{scene_id}/reader-comments")
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data["comments"]), 3)
        self.assertIsNone(data.get("last_error_code"))
