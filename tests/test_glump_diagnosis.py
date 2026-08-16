"""Glump ER diagnosis: rule-based routing, log table, no Gemini."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import gemini_client


class GlumpDiagnosisTests(unittest.TestCase):
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

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, object]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=10
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

    def test_migration_creates_table_and_records_version(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 37"
            ).fetchone()
            self.assertIsNotNone(row)
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("glump_diagnosis_logs", tables)
            cols = {
                str(item[1])
                for item in connection.execute(
                    "PRAGMA table_info(glump_diagnosis_logs)"
                )
            }
            self.assertEqual(
                cols,
                {
                    "id",
                    "work_id",
                    "q1_answer",
                    "q2_answer",
                    "recommended_tool",
                    "created_at",
                },
            )

    def test_rule_table_without_gemini(self) -> None:
        perfectionism = app.diagnose_glump("perfectionism")
        self.assertEqual(perfectionism["recommended_tool"], "ten_min_sprint")
        self.assertNotIn("show_rest_choice", perfectionism)

        self_doubt = app.diagnose_glump("self_doubt")
        self.assertEqual(self_doubt["recommended_tool"], "mental_vitamin")

        burnout = app.diagnose_glump("burnout")
        self.assertIsNone(burnout["recommended_tool"])
        self.assertTrue(burnout["show_rest_choice"])
        self.assertIn("300자", burnout["message"])

        blocked = app.diagnose_glump("block", "event")
        self.assertEqual(blocked["recommended_tool"], "wildcard_spark")
        self.assertEqual(blocked["q2_answer"], "event")

        with self.assertRaises(ValueError) as raised:
            app.diagnose_glump("block")
        self.assertEqual(str(raised.exception), "다음 질문에 답해주세요")

        self.assertEqual(
            app.diagnose_glump("block", "sentence_struggle")["recommended_tool"],
            "fill_blank_game",
        )
        self.assertEqual(
            app.diagnose_glump("block", "start")["recommended_tool"],
            "lucky_sentence",
        )
        self.assertEqual(
            app.diagnose_glump("block", "together")["recommended_tool"],
            "pingpong_relay",
        )

    def test_post_four_cases_logs_and_skips_gemini(self) -> None:
        calls: list[object] = []

        def _boom(*args: object, **kwargs: object) -> str:
            calls.append((args, kwargs))
            raise AssertionError("Glump diagnosis must not call Gemini")

        cases = [
            (
                {"work_id": "1", "q1_answer": "perfectionism"},
                "ten_min_sprint",
                False,
            ),
            (
                {"work_id": "1", "q1_answer": "self_doubt"},
                "mental_vitamin",
                False,
            ),
            (
                {"work_id": "1", "q1_answer": "burnout"},
                None,
                True,
            ),
            (
                {"work_id": "1", "q1_answer": "block", "q2_answer": "event"},
                "wildcard_spark",
                False,
            ),
        ]
        with patch.object(gemini_client, "generate_text", side_effect=_boom):
            for payload, tool, rest in cases:
                status, data = self.request("POST", "/api/glump/diagnose", payload)
                self.assertEqual(status, 200, data)
                self.assertEqual(data.get("recommended_tool"), tool)
                self.assertTrue(str(data.get("message") or "").strip())
                if rest:
                    self.assertTrue(data.get("show_rest_choice"))
                else:
                    self.assertNotIn("show_rest_choice", data)
        self.assertEqual(calls, [])

        with app.database() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM glump_diagnosis_logs"
            ).fetchone()[0]
            self.assertEqual(count, 4)
            stamp = connection.execute(
                "SELECT created_at FROM glump_diagnosis_logs LIMIT 1"
            ).fetchone()[0]
            self.assertRegex(
                stamp,
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$",
            )

        status, stats = self.request("GET", "/api/glump/diagnosis-stats")
        self.assertEqual(status, 200, stats)
        self.assertEqual(stats["q1_answer"]["perfectionism"], 1)
        self.assertEqual(stats["q1_answer"]["self_doubt"], 1)
        self.assertEqual(stats["q1_answer"]["burnout"], 1)
        self.assertEqual(stats["q1_answer"]["block"], 1)
        self.assertEqual(stats["q2_answer"]["event"], 1)
        self.assertEqual(stats["q2_answer"]["start"], 0)

    def test_block_without_q2_is_400(self) -> None:
        status, data = self.request(
            "POST",
            "/api/glump/diagnose",
            {"work_id": "1", "q1_answer": "block"},
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(data.get("error"), "다음 질문에 답해주세요")
        with app.database() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM glump_diagnosis_logs"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_ui_shows_doctor_tori_on_home(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        idle = root / "assets" / "glump" / "tori-doctor-idle.png"
        anim = root / "assets" / "glump" / "tori-doctor.gif"
        home_idx = html.find('id="glumpErStepHome"')
        dx_idx = html.find('id="glumpErStartDiagnosis"')
        tori_idx = html.find('id="glumpErHomeTori"')
        self.assertGreater(home_idx, 0)
        self.assertGreater(tori_idx, home_idx)
        self.assertGreater(dx_idx, tori_idx)
        self.assertIn("/assets/glump/tori-doctor.gif", html)
        self.assertIn("function playGlumpHomeToriIntro()", js)
        self.assertIn("playGlumpHomeToriIntro()", js)
        self.assertTrue(idle.is_file())
        self.assertTrue(anim.is_file())
        from PIL import Image

        with Image.open(idle) as still:
            self.assertEqual(still.mode, "RGBA", idle.name)
            extrema = still.getchannel("A").getextrema()
            self.assertLess(extrema[0], 20, idle.name)
            self.assertGreater(extrema[1], 200, idle.name)
        with Image.open(anim) as gif:
            self.assertEqual(gif.format, "GIF")
            self.assertTrue(getattr(gif, "is_animated", False), "doctor gif should animate")
            self.assertGreater(getattr(gif, "n_frames", 1), 20)
            gif.seek(0)
            corner = gif.convert("RGBA").getpixel((0, 0))
            self.assertEqual(corner[3], 0, "gif corners should be transparent")
