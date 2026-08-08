"""흥행 공식 참고: linked_success_profile_id + prompt wrap (a–d)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client
import success_pattern


class SuccessProfileRefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.orig_data = app.DATA_DIR
        self.orig_db = app.DATABASE_PATH
        app.DATA_DIR = Path(self.td.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.orig_data
        app.DATABASE_PATH = self.orig_db
        self.td.cleanup()

    def req(self, method: str, path: str, payload: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=120)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body, headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        return resp.status, data

    def _seed_project(self) -> dict:
        st, p = self.req(
            "POST",
            "/api/projects",
            {"title": "신작 테스트", "main_genre": "판타지"},
        )
        self.assertEqual(st, 201, p)
        st, ch = self.req("POST", f"/api/projects/{p['id']}/chapters", {"title": "1장"})
        self.assertEqual(st, 201, ch)
        st, sc = self.req(
            "POST",
            f"/api/chapters/{ch['id']}/scenes",
            {"title": "1화"},
        )
        self.assertEqual(st, 201, sc)
        st, detail = self.req("GET", f"/api/scenes/{sc['id']}")
        content = (
            "<p>주인공 묵연은 검을 집어 들었다. 강서연은 스마트폰을 확인했다. "
            "묵연이 카페를 떠올렸다. 설정이 흔들리는 장면이다.</p>" * 3
        )
        st, saved = self.req(
            "PUT",
            f"/api/scenes/{sc['id']}",
            {
                "title": "1화",
                "status": "draft",
                "content_md": content,
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(st, 200, saved)
        return {"project": p, "chapter": ch, "scene": sc}

    def _create_profile(self) -> dict:
        sections = [
            {
                "key": "front",
                "start_ep": 1,
                "end_ep": 2,
                "episodes": [
                    {"title": "1화", "text": "훅으로 끝나는 짧은 회차. 대사가 많다. " * 20},
                    {"title": "2화", "text": "잔잔한 마무리. 심리 묘사. " * 20},
                ],
            }
        ]
        st, result = self.req(
            "POST",
            "/api/success-pattern/run",
            {
                "work_title": "흥행 완결작",
                "total_chapters": 100,
                "sections": sections,
                "dry_run": True,
            },
        )
        self.assertEqual(st, 200, result)
        return result["profile"]

    def test_a_unlinked_no_profile_id(self) -> None:
        """(a) 프로파일 미연결 → linked_success_profile_id 없음 (체크박스 숨김 조건)."""
        seed = self._seed_project()
        pid = seed["project"]["id"]
        st, outline = self.req("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(st, 200)
        proj = outline.get("project") or {}
        self.assertIn(
            proj.get("linked_success_profile_id"),
            (None, 0, "", False),
        )
        st, listed = self.req("GET", "/api/projects")
        row = next(p for p in listed if p["id"] == pid)
        self.assertIn(row.get("linked_success_profile_id"), (None, 0, "", False))

    def test_prompt_wrap_and_order(self) -> None:
        base = "[본문]\n테스트"
        with_index = (
            "\n[프로젝트 누적 정보 - 참고용]\n등장인물: []\n"
            + "task\n\n"
            + base
        )
        profile = {
            "reader_popularity_factors": ["회차 말미 훅", "캐릭터 일관성"],
            "editor_popularity_factors": ["설정 일관성"],
            "must_follow_factors": ["캐릭터 말투 유지"],
            "hook_style": "궁금증 훅",
            "pacing_pattern": "빠른 전개",
            "dialogue_narration_balance": "대사 비중 높음",
            "style_signature": "담백한 문체",
        }
        wrapped = success_pattern.build_task_prompt_with_success_profile(with_index, profile)
        self.assertTrue(wrapped.startswith("[흥행 공식 참고"))
        self.assertIn("회차 말미 훅", wrapped)
        self.assertIn("설정 일관성", wrapped)
        self.assertIn("캐릭터 말투 유지", wrapped)
        self.assertIn(with_index, wrapped)
        # unchecked path
        self.assertEqual(
            success_pattern.build_task_prompt_with_success_profile(with_index, None),
            with_index,
        )

    def test_b_c_link_and_assist_paths(self) -> None:
        """(b)(c) 연결 후 worldscan/brainstorm — 프롬프트 래퍼·미체크 동일성."""
        seed = self._seed_project()
        pid = seed["project"]["id"]
        profile = self._create_profile()
        prof_id = profile["id"]
        # link
        st, linked = self.req(
            "PUT",
            f"/api/projects/{pid}/settings",
            {"linked_success_profile_id": prof_id},
        )
        self.assertEqual(st, 200, linked)
        self.assertEqual(linked.get("linked_success_profile_id"), prof_id)

        st, outline = self.req("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(outline["project"].get("linked_success_profile_id"), prof_id)

        # Profile factors present for wrap
        st, full = self.req("GET", f"/api/success-pattern/profiles/{prof_id}")
        self.assertEqual(st, 200)
        factors = full.get("profile") or {}
        self.assertTrue(
            factors.get("reader_popularity_factors")
            or factors.get("hook_style")
            or factors.get("summary")
        )

        scene_plain = (
            "묵연이 스마트폰을 켜고 카페 위치를 검색했다. "
            "검객인데 현대 문물을 자연스럽게 쓴다. 설정 붕괴 후보."
        )
        base_task = "설정 붕괴를 점검하세요."
        with_index = success_pattern.build_task_prompt_with_success_profile.__doc__  # noqa: keep import used
        indexed = (
            f"[프로젝트 누적 정보 - 참고용]\n등장인물: [\"묵연\"]\n\n"
            f"{base_task}\n\n[본문]\n{scene_plain}"
        )
        # (b) checked wrap
        checked = success_pattern.build_task_prompt_with_success_profile(
            indexed, factors
        )
        self.assertIn("[흥행 공식 참고", checked)
        self.assertIn(indexed, checked)
        self.assertTrue(checked.find("[흥행 공식 참고") < checked.find("[프로젝트 누적 정보"))

        # (c) unchecked = same as indexed
        unchecked = success_pattern.build_task_prompt_with_success_profile(indexed, None)
        self.assertEqual(unchecked, indexed)

        # Live assist when Gemini available: inject via indexed_prompt with profile wrap
        if not gemini_client.is_configured():
            self.skipTest("GEMINI_API_KEY 없음 — 래퍼 단위 (b)(c) 통과, 라이브 스킵")

        st, worldscan = self.req(
            "POST",
            "/api/ai/assist",
            {
                "mode": "worldscan",
                "project_id": pid,
                "scene_content": scene_plain,
                "indexed_prompt": checked,
                "main_genre": "판타지",
                "purpose": "general_novel",
            },
        )
        self.assertEqual(st, 200, worldscan)
        text = str(worldscan.get("text") or "")
        self.assertGreater(len(text), 20)

        # (d) brainstorm with profile wrap
        brain_indexed = (
            "[프로젝트 누적 정보 - 참고용]\n등장인물: [\"주인공\"]\n\n"
            "미니 악역 추가 아이디어를 제안하세요.\n\n[본문]\n주인공이 마을에 도착했다."
        )
        brain_prompt = success_pattern.build_task_prompt_with_success_profile(
            brain_indexed, factors
        )
        self.assertIn("[흥행 공식 참고", brain_prompt)
        st, brain = self.req(
            "POST",
            "/api/ai/assist",
            {
                "mode": "brainstorm",
                "project_id": pid,
                "scene_content": "주인공이 마을에 도착했다.",
                "user_topic": "미니 악역 추가 아이디어",
                "indexed_prompt": brain_prompt,
                "main_genre": "판타지",
                "purpose": "general_novel",
            },
        )
        self.assertEqual(st, 200, brain)
        self.assertGreater(len(str(brain.get("text") or "")), 20)

        # unlink
        st, unlinked = self.req(
            "PUT",
            f"/api/projects/{pid}/settings",
            {"linked_success_profile_id": None},
        )
        self.assertEqual(st, 200, unlinked)
        self.assertIsNone(unlinked.get("linked_success_profile_id"))

    def test_migration_026(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 26"
            ).fetchone()
            self.assertIsNotNone(row)
            cols = {
                r[1]
                for r in connection.execute("PRAGMA table_info(project)").fetchall()
            }
            self.assertIn("linked_success_profile_id", cols)


if __name__ == "__main__":
    unittest.main()
