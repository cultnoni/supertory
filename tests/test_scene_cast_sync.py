"""HTTP checks for scene cast auto-link (appearing vs mentioned)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


class SceneCastSyncTests(unittest.TestCase):
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

    def test_sync_cast_links_registered_appearing_only(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "등장 테스트", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        project_id = project["id"]
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1화"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "장면"})
        self.assertEqual(status, 201)

        status, seoyun = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "서윤"})
        self.assertEqual(status, 201)
        status, _ = self.request("POST", f"/api/characters/{seoyun['id']}/aliases", {"alias": "여우"})
        self.assertEqual(status, 201)
        status, haein = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "해인"})
        self.assertEqual(status, 201)
        other = self.request("POST", "/api/projects", {"title": "다른 작품", "main_genre": "판타지"})[1]
        status, stranger = self.request("POST", f"/api/projects/{other['id']}/characters", {"name": "이방인"})
        self.assertEqual(status, 201)
        status, _ = self.request("POST", f"/api/characters/{stranger['id']}/aliases", {"alias": "유령"})
        self.assertEqual(status, 201)

        text = (
            "서윤이 문을 열고 들어왔다.\n"
            "해인이 어디 갔는지 떠올렸다.\n"
            "민재가 창밖을 보았다.\n"
            "유령은 소문만 돌았다."
        )
        status, result = self.request(
            "POST",
            f"/api/scenes/{scene['id']}/sync-cast",
            {"content_md": text},
        )
        self.assertEqual(status, 200)
        roles = {row["character_id"]: row["appearance_role"] for row in result["members"]}
        self.assertEqual(roles[seoyun["id"]], "supporting")
        self.assertNotIn(haein["id"], roles)
        self.assertEqual(result["created"], [])
        self.assertNotIn(stranger["id"], roles)

        status, listed = self.request("GET", f"/api/projects/{project_id}/characters")
        self.assertEqual(status, 200)
        names = {row["name"] for row in listed}
        self.assertNotIn("민재", names)
        self.assertNotIn("유령", names)

        status, other_chars = self.request("GET", f"/api/projects/{other['id']}/characters")
        self.assertEqual(status, 200)
        self.assertEqual(other_chars[0]["aliases"], ["유령"])
        self.assertEqual(len(other_chars), 1)

        status, members = self.request("GET", f"/api/scenes/{scene['id']}/characters")
        self.assertEqual(status, 200)
        self.assertEqual(
            {row["character_id"]: row["appearance_role"] for row in members},
            roles,
        )

    def test_sync_cast_keeps_extra_ids_and_honors_suppressed(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "추가 인물", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        project_id = project["id"]
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1화"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "장면"})
        self.assertEqual(status, 201)
        status, seoyun = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "서윤"})
        self.assertEqual(status, 201)
        status, extra = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "카엘"})
        self.assertEqual(status, 201)

        status, result = self.request(
            "POST",
            f"/api/scenes/{scene['id']}/sync-cast",
            {
                "content_md": "서윤이 문을 열고 들어왔다.",
                "extra_character_ids": [extra["id"]],
                "suppressed_ids": [seoyun["id"]],
            },
        )
        self.assertEqual(status, 200)
        roles = {row["character_id"]: row["appearance_role"] for row in result["members"]}
        self.assertNotIn(seoyun["id"], roles)
        self.assertEqual(roles[extra["id"]], "supporting")
        self.assertEqual(result["created"], [])

    def test_sync_cast_does_not_link_name_only_mentions(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "언급 제외", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        project_id = project["id"]
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1화"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "장면"})
        self.assertEqual(status, 201)
        status, haein = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "해인"})
        self.assertEqual(status, 201)

        status, result = self.request(
            "POST",
            f"/api/scenes/{scene['id']}/sync-cast",
            {"content_md": "해인이 어디 갔는지 떠올렸다."},
        )
        self.assertEqual(status, 200)
        roles = {row["character_id"]: row["appearance_role"] for row in result["members"]}
        self.assertNotIn(haein["id"], roles)
        self.assertEqual(result["created"], [])

    def test_sync_cast_does_not_create_or_count_scene_nouns(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "명사 제외", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        project_id = project["id"]
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1화"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "장면"})
        self.assertEqual(status, 201)

        status, result = self.request(
            "POST",
            f"/api/scenes/{scene['id']}/sync-cast",
            {"content_md": "그림자가 벽에 늘어졌다. 표정이 굳었다."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["members"], [])
        self.assertEqual(result["created"], [])
        status, listed = self.request("GET", f"/api/projects/{project_id}/characters")
        self.assertEqual(status, 200)
        self.assertEqual(listed, [])

    def test_sync_cast_does_not_create_unregistered_names(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "동사 제외", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        project_id = project["id"]
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1화"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "장면"})
        self.assertEqual(status, 201)

        status, result = self.request(
            "POST",
            f"/api/scenes/{scene['id']}/sync-cast",
            {
                "content_md": (
                    "언덕에서 내려오는 외양이 달랐다. "
                    "그 지칭하는 것이 해당하는 조건이었다. "
                    "성년이 되었다. 몸가짐을 바르게 했다. "
                    "민재가 창밖을 보았다."
                ),
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["created"], [])
        self.assertEqual(result["members"], [])
        listed = self.request("GET", f"/api/projects/{project_id}/characters")[1]
        names = {row["name"] for row in listed}
        self.assertTrue(
            {"내려오", "외양", "지칭하", "것", "해당하", "성년", "몸가짐", "민재"}.isdisjoint(names)
        )

    def test_cast_candidates_uses_gemini_and_skips_known(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "후보", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        project_id = project["id"]
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1화"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "장면"})
        self.assertEqual(status, 201)
        status, _ = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "서윤"})
        self.assertEqual(status, 201)

        original_configured = gemini_client.is_configured
        original_generate = gemini_client.generate_text
        gemini_client.is_configured = lambda: True  # type: ignore[method-assign]

        def _fake(_prompt, **_kwargs):
            return '{"names": ["하린", "서윤", "몸가짐"]}'

        gemini_client.generate_text = _fake  # type: ignore[method-assign]
        try:
            status, result = self.request(
                "POST",
                f"/api/scenes/{scene['id']}/cast-candidates",
                {"content_md": "하린이 문을 열고 들어왔다. 서윤이 말했다."},
            )
        finally:
            gemini_client.is_configured = original_configured  # type: ignore[method-assign]
            gemini_client.generate_text = original_generate  # type: ignore[method-assign]
        self.assertEqual(status, 200)
        self.assertEqual(result["candidates"], ["하린", "몸가짐"])
        listed = self.request("GET", f"/api/projects/{project_id}/characters")[1]
        self.assertEqual({row["name"] for row in listed}, {"서윤"})

    def test_cast_candidates_fail_quietly(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "실패", "main_genre": "판타지"})
        self.assertEqual(status, 201)
        project_id = project["id"]
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1화"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "장면"})
        self.assertEqual(status, 201)
        original_configured = gemini_client.is_configured
        original_generate = gemini_client.generate_text
        gemini_client.is_configured = lambda: True  # type: ignore[method-assign]

        def _boom(*_args, **_kwargs):
            raise gemini_client.GeminiError("boom")

        gemini_client.generate_text = _boom  # type: ignore[method-assign]
        try:
            status, result = self.request(
                "POST",
                f"/api/scenes/{scene['id']}/cast-candidates",
                {"content_md": "하린이 들어왔다."},
            )
        finally:
            gemini_client.is_configured = original_configured  # type: ignore[method-assign]
            gemini_client.generate_text = original_generate  # type: ignore[method-assign]
        self.assertEqual(status, 200)
        self.assertEqual(result["candidates"], [])
