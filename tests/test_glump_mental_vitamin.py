"""Glump ER Mental Vitamin: highlight extraction, autosave hook, GET pick."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


SAMPLE_MOMENTS = [
    {
        "type": "dialogue",
        "excerpt": "네가 그 편지를 연 순간부터, 이미 판은 바뀌었다.",
        "reason": "정체가 들킨 긴장이 한 줄에 모여 있어요.",
    },
    {
        "type": "description",
        "excerpt": "연회장 기둥 뒤로 숨소리가 가늘어졌다.",
        "reason": "공간과 숨이 맞물려 리듬이 좋아요.",
    },
    {
        "type": "scene",
        "excerpt": "도망치면 살 수 있지만 오빠의 진실을 놓친다.",
        "reason": "선택지가 감정선과 전개를 한꺼번에 밀어 올립니다.",
    },
]


def _moments_json(items=None) -> str:
    return json.dumps(items if items is not None else SAMPLE_MOMENTS, ensure_ascii=False)


class GlumpMentalVitaminTests(unittest.TestCase):
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
        self.calls: list[object] = []
        self._orig_generate = gemini_client.generate_text
        self._orig_configured = gemini_client.is_configured
        self._orig_schedule = app.schedule_glump_highlight_analysis

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system})
            return _moments_json()

        gemini_client.generate_text = _fake  # type: ignore[method-assign]
        gemini_client.is_configured = lambda: True  # type: ignore[method-assign]

        def _sync(scene_id: int, content: str) -> None:
            app.maybe_extract_glump_highlights(scene_id, content)

        app.schedule_glump_highlight_analysis = _sync

    def tearDown(self) -> None:
        gemini_client.generate_text = self._orig_generate  # type: ignore[method-assign]
        gemini_client.is_configured = self._orig_configured  # type: ignore[method-assign]
        app.schedule_glump_highlight_analysis = self._orig_schedule
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
            "127.0.0.1", self.server.server_port, timeout=15
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

    def _make_scene(self) -> tuple[int, int]:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "비타민 테스트", "main_genre": "romfant", "sub_genre": "high"},
        )
        self.assertEqual(status, 201, project)
        pid = int(project["id"])
        status, chapter = self.request(
            "POST", f"/api/projects/{pid}/chapters", {"title": "1장"}
        )
        self.assertEqual(status, 201, chapter)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.assertEqual(status, 201, scene)
        return pid, int(scene["id"])

    def _save(self, scene_id: int, text: str) -> tuple[int, object]:
        status, detail = self.request("GET", f"/api/scenes/{scene_id}")
        self.assertEqual(status, 200, detail)
        return self.request(
            "PUT",
            f"/api/scenes/{scene_id}",
            {
                "title": "1화",
                "status": "draft",
                "content_md": text,
                "row_version": detail["row_version"],
            },
        )

    def test_migration_039_creates_tables(self) -> None:
        with app.database() as connection:
            versions = {
                row[0]
                for row in connection.execute("SELECT version FROM schema_migration")
            }
            self.assertIn(39, versions)
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("glump_highlight_moments", tables)
            self.assertIn("glump_highlight_progress", tables)

    def test_prompt_reuses_tory_core_and_json_contract(self) -> None:
        system, user = app._highlight_extraction_prompt("숨소리가 죽었다.")
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertEqual(system, core)
        self.assertIn("[Task Instruction]", user)
        self.assertIn("대사", user)
        self.assertIn("묘사", user)
        self.assertIn("장면", user)
        self.assertIn('"type": "dialogue"', user)
        self.assertIn("숨소리가 죽었다.", user)
        self.assertIn("빈 배열", user)

    def test_parse_empty_and_partial(self) -> None:
        self.assertEqual(app._parse_highlight_moments("[]"), [])
        parsed = app._parse_highlight_moments(_moments_json(SAMPLE_MOMENTS[:1]))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["type"], "dialogue")

    def test_short_save_does_not_call_gemini(self) -> None:
        _pid, sid = self._make_scene()
        status, data = self._save(sid, "짧은 문장")
        self.assertEqual(status, 200, data)
        self.assertEqual(self.calls, [])
        with app.database() as connection:
            n = connection.execute(
                "SELECT COUNT(*) FROM glump_highlight_moments"
            ).fetchone()[0]
        self.assertEqual(n, 0)

    def test_save_500_plus_stores_moments_and_progress(self) -> None:
        pid, sid = self._make_scene()
        text = "가" * 520
        status, data = self._save(sid, text)
        self.assertEqual(status, 200, data)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("[Tory Core Identity]", str(self.calls[0]["system"]))
        with app.database() as connection:
            rows = connection.execute(
                "SELECT work_id, episode_id, episode_order, moment_type, excerpt "
                "FROM glump_highlight_moments ORDER BY moment_type"
            ).fetchall()
            progress = connection.execute(
                "SELECT last_analyzed_length FROM glump_highlight_progress "
                "WHERE episode_id = ?",
                (str(sid),),
            ).fetchone()
        self.assertEqual(len(rows), 3)
        self.assertEqual(str(rows[0]["work_id"]), str(pid))
        self.assertEqual(str(rows[0]["episode_id"]), str(sid))
        self.assertEqual(int(rows[0]["episode_order"]), 1)
        self.assertEqual(int(progress["last_analyzed_length"]), 520)
        status, vitamin = self.request(
            "GET", f"/api/glump/mental-vitamin?work_id={pid}"
        )
        self.assertEqual(status, 200, vitamin)
        self.assertFalse(vitamin.get("empty"))
        self.assertEqual(len(vitamin.get("moments") or []), 3)
        kinds = {item["type"] for item in vitamin["moments"]}
        self.assertEqual(kinds, {"dialogue", "description", "scene"})
        self.assertEqual(vitamin["moments"][0]["episode_order"], 1)

    def test_gemini_failure_does_not_break_save(self) -> None:
        def _boom(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            raise gemini_client.GeminiError("down")

        gemini_client.generate_text = _boom  # type: ignore[method-assign]
        _pid, sid = self._make_scene()
        status, data = self._save(sid, "나" * 600)
        self.assertEqual(status, 200, data)
        self.assertTrue(data.get("ok"))
        with app.database() as connection:
            n = connection.execute(
                "SELECT COUNT(*) FROM glump_highlight_moments"
            ).fetchone()[0]
            progress = connection.execute(
                "SELECT last_analyzed_length FROM glump_highlight_progress "
                "WHERE episode_id = ?",
                (str(sid),),
            ).fetchone()
        self.assertEqual(n, 0)
        self.assertIsNone(progress)

    def test_shrink_clamps_then_net_growth_reanalyzes(self) -> None:
        _pid, sid = self._make_scene()
        self.assertEqual(self._save(sid, "가" * 520)[0], 200)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self._save(sid, "나" * 80)[0], 200)
        self.assertEqual(len(self.calls), 1)
        with app.database() as connection:
            last = connection.execute(
                "SELECT last_analyzed_length FROM glump_highlight_progress "
                "WHERE episode_id = ?",
                (str(sid),),
            ).fetchone()["last_analyzed_length"]
        self.assertEqual(int(last), 80)
        self.assertEqual(self._save(sid, "다" * 590)[0], 200)
        self.assertEqual(len(self.calls), 2)

    def test_get_empty_message_and_log(self) -> None:
        pid, _sid = self._make_scene()
        status, data = self.request(
            "GET", f"/api/glump/mental-vitamin?work_id={pid}"
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data.get("empty"))
        self.assertIn("아직 모아둔 명장면이 없어요", data.get("message") or "")
        with app.database() as connection:
            row = connection.execute(
                "SELECT tool_id FROM glump_tool_logs WHERE tool_id = 'mental_vitamin'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_get_picks_diverse_types_newest_first(self) -> None:
        pid, sid = self._make_scene()
        t_old = "2026-01-01T00:00:00.000001Z"
        t_mid = "2026-01-02T00:00:00.000001Z"
        t_new = "2026-01-03T00:00:00.000001Z"
        t_newest = "2026-01-03T00:00:00.000002Z"
        with app.database() as connection:
            connection.execute(
                """
                INSERT INTO glump_highlight_moments
                    (id, work_id, episode_id, episode_order, moment_type,
                     excerpt, reason, created_at)
                VALUES
                    ('a', ?, '10', 2, 'dialogue', '옛 대사', 'old', ?),
                    ('c', ?, ?, 3, 'dialogue', '같은 화 대사2', 'dup', ?),
                    ('b', ?, ?, 3, 'dialogue', '최신 대사', 'new d', ?),
                    ('d', ?, ?, 3, 'description', '최신 묘사', 'new desc', ?),
                    ('e', ?, '10', 2, 'scene', '이전 화 장면', 'scene', ?)
                """,
                (
                    str(pid), t_old,
                    str(pid), str(sid), t_mid,
                    str(pid), str(sid), t_new,
                    str(pid), str(sid), t_newest,
                    str(pid), t_old,
                ),
            )
        status, data = self.request(
            "GET", f"/api/glump/mental-vitamin?work_id={pid}"
        )
        self.assertEqual(status, 200, data)
        self.assertFalse(data.get("empty"))
        moments = data.get("moments") or []
        kinds = [item["type"] for item in moments]
        self.assertEqual(set(kinds), {"dialogue", "description", "scene"})
        by_type = {item["type"]: item for item in moments}
        self.assertEqual(by_type["dialogue"]["excerpt"], "최신 대사")
        self.assertEqual(by_type["description"]["excerpt"], "최신 묘사")
        self.assertEqual(by_type["scene"]["excerpt"], "이전 화 장면")
        self.assertEqual(by_type["scene"]["episode_order"], 2)
