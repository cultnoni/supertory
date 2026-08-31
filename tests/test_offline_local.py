"""Local writing must work with no internet; cloud/AI must fail clearly."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import app
import gemini_client
from world_import_analysis import compose_worldbuilding_md


class OfflineLocalHttpTests(unittest.TestCase):
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

    def test_local_writing_stack_without_cloud(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "오프라인 작품", "main_genre": "판타지"}
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

        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200, detail)
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": "1화",
                "status": "draft",
                "synopsis_md": "첫 만남",
                "notes_md": "",
                "content_md": "비가 내리던 날, 두 사람은 처음 만났다.",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200, saved)
        status, reloaded = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200, reloaded)
        self.assertIn("비가 내리던 날", reloaded["content_md"])

        status, created = self.request(
            "POST", f"/api/projects/{pid}/characters", {"name": "비비"}
        )
        self.assertEqual(status, 201, created)
        cid = int(created["id"])
        status, char_detail = self.request("GET", f"/api/characters/{cid}")
        self.assertEqual(status, 200, char_detail)
        status, _ = self.request(
            "PUT",
            f"/api/characters/{cid}",
            {
                "name": "비비",
                "profile_md": "빨간 머리",
                "row_version": char_detail["character"]["row_version"],
            },
        )
        self.assertEqual(status, 200)

        status, item = self.request(
            "POST",
            f"/api/projects/{pid}/items",
            {"name": "흑염검", "description": "달빛을 먹는 칼"},
        )
        self.assertEqual(status, 201, item)

        world = compose_worldbuilding_md({"locale": "해안 도시"})
        status, _ = self.request(
            "PUT", f"/api/projects/{pid}/settings", {"worldbuilding_md": world}
        )
        self.assertEqual(status, 200)

        status, other = self.request(
            "POST", f"/api/projects/{pid}/characters", {"name": "엔케"}
        )
        self.assertEqual(status, 201, other)
        status, rel = self.request(
            "POST",
            f"/api/projects/{pid}/character-relations",
            {
                "character_a_id": cid,
                "character_b_id": int(other["id"]),
                "label": "연인",
            },
        )
        self.assertEqual(status, 201, rel)
        status, canvas = self.request("GET", f"/api/projects/{pid}/character-canvas")
        self.assertEqual(status, 200, canvas)
        self.assertGreaterEqual(len(canvas.get("relations") or []), 1)

        status, hits = self.request(
            "GET", f"/api/projects/{pid}/settings-search?q={quote('흑염검')}"
        )
        self.assertEqual(status, 200, hits)
        blob = json.dumps(hits, ensure_ascii=False)
        self.assertIn("흑염검", blob)

        status, presets = self.request("GET", "/api/typeset/presets")
        self.assertEqual(status, 200, presets)
        self.assertIn("munpia", (presets.get("presets") or {}))

        status, sequel = self.request(
            "POST",
            "/api/projects",
            {
                "title": "속편",
                "main_genre": "판타지",
                "inherit_from_project_id": pid,
                "inherit_chronicle": False,
            },
        )
        self.assertEqual(status, 201, sequel)
        self.assertEqual(sequel.get("inherited_from_title"), "오프라인 작품")

    def test_external_firewall_keeps_local_save_and_fails_gemini(self) -> None:
        """Treat non-loopback sockets as unreachable (firewall / true offline)."""
        import socket

        real_create = socket.create_connection

        def guarded(address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) else str(address)
            if host in {"127.0.0.1", "localhost", "::1"}:
                return real_create(address, *args, **kwargs)
            raise OSError("Network is unreachable")

        with patch("socket.create_connection", side_effect=guarded):
            status, project = self.request(
                "POST", "/api/projects", {"title": "방화벽 작품", "main_genre": "판타지"}
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
            status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
            self.assertEqual(status, 200, detail)
            status, saved = self.request(
                "PUT",
                f"/api/scenes/{scene['id']}",
                {
                    "title": "1화",
                    "status": "draft",
                    "content_md": "외부망이 없어도 저장됩니다.",
                    "row_version": detail["row_version"],
                },
            )
            self.assertEqual(status, 200, saved)

            with (
                patch.object(gemini_client, "is_configured", return_value=True),
                patch.object(
                    gemini_client,
                    "get_env",
                    side_effect=lambda key, default=None: (
                        "fake-key"
                        if key == "GEMINI_API_KEY"
                        else (default or gemini_client.DEFAULT_MODEL)
                    ),
                ),
            ):
                started = time.perf_counter()
                with self.assertRaises(gemini_client.GeminiError) as ctx:
                    gemini_client.generate_text("이어 써 주세요")
                elapsed = time.perf_counter() - started
            self.assertEqual(ctx.exception.code, "network")
            self.assertIn("인터넷 연결이 필요해요", str(ctx.exception))
            self.assertLess(elapsed, 5.0)

            def boom(*_args, **_kwargs):
                raise gemini_client.GeminiError(
                    gemini_client.NETWORK_USER_MESSAGE, code="network"
                )

            with (
                patch.object(gemini_client, "is_configured", return_value=True),
                patch.object(gemini_client, "generate_text", side_effect=boom),
            ):
                status, payload = self.request(
                    "POST",
                    "/api/ai/assist",
                    {"mode": "continue", "scene_content": "비가 내렸다."},
                )
            self.assertEqual(status, 400, payload)
            self.assertIn("인터넷 연결이 필요해요", str(payload.get("error") or payload))

    def test_ai_assist_reports_internet_needed(self) -> None:
        def boom(*_args, **_kwargs):
            raise gemini_client.GeminiError(
                gemini_client.NETWORK_USER_MESSAGE, code="network"
            )

        with (
            patch.object(gemini_client, "is_configured", return_value=True),
            patch.object(gemini_client, "generate_text", side_effect=boom),
        ):
            status, payload = self.request(
                "POST",
                "/api/ai/assist",
                {"mode": "continue", "scene_content": "비가 내렸다."},
            )
        self.assertEqual(status, 400, payload)
        self.assertIn("인터넷 연결이 필요해요", str(payload.get("error") or payload))

    def test_relation_suggest_reports_internet_needed(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "관계 테스트", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201, project)
        pid = int(project["id"])
        for name in ("비비", "엔케"):
            status, created = self.request(
                "POST", f"/api/projects/{pid}/characters", {"name": name}
            )
            self.assertEqual(status, 201, created)

        def boom(*_args, **_kwargs):
            raise gemini_client.GeminiError(
                gemini_client.NETWORK_USER_MESSAGE, code="network"
            )

        with (
            patch.object(gemini_client, "is_configured", return_value=True),
            patch.object(gemini_client, "generate_text", side_effect=boom),
        ):
            status, payload = self.request(
                "POST", f"/api/projects/{pid}/character-relations/suggest"
            )
        self.assertEqual(status, 400, payload)
        self.assertIn("인터넷 연결이 필요해요", str(payload.get("error") or payload))

    def test_init_desktop_sync_does_not_block_listen(self) -> None:
        def hang_restore():
            time.sleep(20)
            return None

        started = time.perf_counter()
        with patch.object(app, "restore_session", side_effect=hang_restore):
            app._init_desktop_sync()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0)

    def test_reading_invite_offline_session_is_503_not_login(self) -> None:
        from sync import auth_session

        status, project = self.request(
            "POST", "/api/projects", {"title": "초대 테스트", "main_genre": "판타지"}
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
        auth_session.save_session("acc", "ref", "user-1", "a@example.com")
        with patch.object(app, "get_current_user", return_value=None):
            status, payload = self.request(
                "POST",
                f"/api/projects/{pid}/reading-invites",
                {"scene_ids": [int(scene["id"])]},
            )
        self.assertEqual(status, 503, payload)
        self.assertIn("인터넷 연결이 필요해요", str(payload.get("error") or payload))
