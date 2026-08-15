"""Glump ER wildcard spark + shared tool log (migration 38)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


SAMPLE_EPISODE = (
    "여주는 연회장 기둥 뒤에 숨은 채 숨소리를 죽였다. 남주는 이미 그녀의 가짜 신분을 "
    "알아챈 것 같았고, 황실 근위대가 입구를 하나씩 잠그기 시작했다. "
    "방금 전 그녀가 훔친 편지에는 반란의 날짜가 적혀 있었다. 그런데 편지 마지막 줄의 필체가 "
    "죽은 오빠의 것과 너무 닮아 있었다. 여주는 편지를 다시 접으며 생각했다. "
    "지금 여기서 도망치면 살 수 있지만, 도망치는 순간 오빠의 진실을 영영 놓친다. "
    "등 뒤에서 익숙한 낮은 목소리가 들렸다. 네가 그 편지를 연 순간부터, 이미 판은 바뀌었다."
)


def _three_events_json() -> str:
    return json.dumps(
        [
            {"title": "편지 속 오빠", "description": "편지의 필체가 살아 있는 오빠의 것으로 드러난다."},
            {"title": "근위대장의 배신", "description": "문을 잠근 근위대장이 반란 쪽 사람이었다."},
            {"title": "남주의 함정", "description": "남주가 일부러 편지를 흘려 여주를 시험한 것이다."},
        ],
        ensure_ascii=False,
    )


class GlumpWildcardSparkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        self.original_generate = gemini_client.generate_text
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.calls: list[object] = []

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system})
            return _three_events_json()

        gemini_client.generate_text = _fake  # type: ignore[method-assign]

    def tearDown(self) -> None:
        gemini_client.generate_text = self.original_generate  # type: ignore[method-assign]
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

    def _make_project(self) -> int:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "와일드카드 테스트", "main_genre": "romfant", "sub_genre": "high"},
        )
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def test_migrations_037_and_038(self) -> None:
        with app.database() as connection:
            versions = {
                row[0]
                for row in connection.execute("SELECT version FROM schema_migration")
            }
            self.assertIn(37, versions)
            self.assertIn(38, versions)
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("glump_diagnosis_logs", tables)
            self.assertIn("glump_tool_logs", tables)

    def test_prompt_reuses_tory_core_and_adds_task(self) -> None:
        system, user = app._wildcard_spark_prompt(
            "로맨스 판타지", SAMPLE_EPISODE, "클라이맥스"
        )
        core = app.SuperToryHandler._tory_core_identity_system_prompt()
        self.assertTrue(system.startswith("[Tory Core Identity]"))
        self.assertIn(core, system)
        self.assertIn("[Dynamic Context]", system)
        self.assertIn("로맨스 판타지", system)
        self.assertIn("여주는 연회장", system)
        self.assertIn("[Task Instruction]", user)
        self.assertIn("가장 파격적이고 예상 못한 사건 3가지", user)
        self.assertIn("클라이맥스", user)
        self.assertIn('"title"', user)

    def test_short_episode_is_400(self) -> None:
        pid = self._make_project()
        status, data = self.request(
            "POST",
            "/api/glump/wildcard-spark",
            {"work_id": str(pid), "episode_content": "너무 짧음"},
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(
            data.get("error"),
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요",
        )
        self.assertEqual(self.calls, [])

    def test_returns_three_events_and_logs(self) -> None:
        pid = self._make_project()
        status, data = self.request(
            "POST",
            "/api/glump/wildcard-spark",
            {
                "work_id": str(pid),
                "episode_content": SAMPLE_EPISODE,
                "stage": "클라이맥스",
            },
        )
        self.assertEqual(status, 200, data)
        events = data.get("events")
        self.assertIsInstance(events, list)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["title"], "편지 속 오빠")
        self.assertEqual(len(self.calls), 1)
        with app.database() as connection:
            row = connection.execute(
                "SELECT work_id, tool_id, created_at FROM glump_tool_logs"
            ).fetchone()
            self.assertEqual(str(row["work_id"]), str(pid))
            self.assertEqual(row["tool_id"], "wildcard_spark")
            self.assertRegex(
                row["created_at"],
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$",
            )

    def test_retries_once_then_500(self) -> None:
        pid = self._make_project()
        n = {"count": 0}

        def _bad(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            n["count"] += 1
            return "이건 JSON이 아닙니다"

        gemini_client.generate_text = _bad  # type: ignore[method-assign]
        status, data = self.request(
            "POST",
            "/api/glump/wildcard-spark",
            {"work_id": str(pid), "episode_content": SAMPLE_EPISODE},
        )
        self.assertEqual(status, 500, data)
        self.assertEqual(data.get("error"), "다시 시도해주세요")
        self.assertEqual(n["count"], 2)

    def test_retry_recovers_on_second_call(self) -> None:
        pid = self._make_project()
        n = {"count": 0}

        def _mixed(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            n["count"] += 1
            if n["count"] == 1:
                return "broken"
            return _three_events_json()

        gemini_client.generate_text = _mixed  # type: ignore[method-assign]
        status, data = self.request(
            "POST",
            "/api/glump/wildcard-spark",
            {"work_id": str(pid), "episode_content": SAMPLE_EPISODE},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data.get("events") or []), 3)
        self.assertEqual(n["count"], 2)

    def test_tool_log_records_sprint_modes_without_gemini(self) -> None:
        pid = self._make_project()
        for tool_id in ("no_edit_timer", "blind_mode", "eraser_seal"):
            status, data = self.request(
                "POST",
                "/api/glump/tool-log",
                {"work_id": str(pid), "tool_id": tool_id},
            )
            self.assertEqual(status, 200, data)
            self.assertEqual(data.get("ok"), True)
            self.assertEqual(data.get("tool_id"), tool_id)
        self.assertEqual(self.calls, [])
        with app.database() as connection:
            rows = connection.execute(
                "SELECT tool_id FROM glump_tool_logs ORDER BY created_at"
            ).fetchall()
        ids = [row["tool_id"] for row in rows]
        self.assertEqual(ids, ["no_edit_timer", "blind_mode", "eraser_seal"])

    def test_tool_log_rejects_unknown_and_missing(self) -> None:
        pid = self._make_project()
        status, data = self.request(
            "POST",
            "/api/glump/tool-log",
            {"work_id": str(pid), "tool_id": "wildcard_spark"},
        )
        self.assertEqual(status, 400, data)
        status, data = self.request(
            "POST",
            "/api/glump/tool-log",
            {"work_id": "", "tool_id": "blind_mode"},
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(self.calls, [])
