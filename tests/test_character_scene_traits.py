"""Scene-complete character trait detection (empty fill, pending badge, history)."""

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
import character_scene_traits
import gemini_client


SCENE_TEXT = (
    "서윤이 문을 열고 들어왔다.\n"
    "지은이 말했다. \"나는 원래 왼손잡이야.\"\n"
    "해인이 어디 갔는지 떠올렸다.\n"
    "민재가 창밖을 보았다."
)


class CharacterSceneTraitUnitTests(unittest.TestCase):
    def test_prompt_includes_judgment_rules(self) -> None:
        system, user = character_scene_traits.build_trait_prompt(
            "서윤이 문을 열고 들어왔다.",
            [{"id": 1, "name": "서윤", "aliases": ["여우"], "profile_md": "", "strengths_md": "", "weaknesses_md": ""}],
        )
        blob = f"{system}\n{user}"
        self.assertIn("명시적 서술 또는 대사로 직접 드러난 것만", blob)
        self.assertIn("일회성 행동과 지속적 특성", blob)
        self.assertIn("원래 그렇다", blob)
        self.assertIn("관계 변화는 서술 자체가 변화를 명시한 경우만", blob)
        self.assertIn("한 줄 소개와 작가 메모는 절대 적지 마세요", blob)
        self.assertIn("확신이 서지 않으면", blob)
        self.assertIn("원래」「평소", blob)
        self.assertIn("이번만 소리를 질렀다", blob)
        self.assertIn("목록에 없는 이름은 만들지 마세요", blob)

    def test_parse_skips_unregistered_and_never_fields(self) -> None:
        appearing = [
            {"id": 7, "name": "서윤", "aliases": ["여우"]},
        ]
        raw = json.dumps({
            "characters": [
                {
                    "id": 7,
                    "name": "서윤",
                    "appearance": "검은 머리",
                    "personality": "차분하다",
                    "short_description": "밤에만 일하는 서점 주인",
                    "author_notes_md": " ent 스포일러",
                    "aliases": ["여우"],
                },
                {
                    "name": "민재",
                    "appearance": "키 크다",
                    "personality": "급하다",
                },
            ]
        })
        parsed = character_scene_traits.parse_trait_json(raw, appearing)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["id"], 7)
        self.assertIn("검은 머리", parsed[0]["fields"]["profile_md"])
        self.assertNotIn("short_description", parsed[0]["fields"])
        self.assertNotIn("author_notes_md", parsed[0]["fields"])


class CharacterSceneTraitHttpTests(unittest.TestCase):
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
            if not comments_busy and not traits_busy:
                break
            time.sleep(0.05)
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
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        data: object
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = raw
        return response.status, data

    def _wait_trait(self, scene_id: int, timeout: float = 8.0) -> dict:
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            status, data = self.request("GET", f"/api/scenes/{scene_id}/trait-analysis")
            self.assertEqual(status, 200, data)
            last = data if isinstance(data, dict) else {}
            st = str(last.get("status") or "")
            if st in {"done", "skipped", "error"} and not last.get("generating"):
                return last
            time.sleep(0.05)
        self.fail(f"인물 특징 분석이 끝나지 않았습니다: {last}")
        return last

    def _make_project_scene(self) -> tuple[int, int]:
        status, project = self.request("POST", "/api/projects", {"title": "특징 테스트", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        status, chapter = self.request("POST", f"/api/projects/{project['id']}/chapters", {"title": "1장"})
        self.assertEqual(status, 201, chapter)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"})
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

    def test_schema_migration_72_on_init(self) -> None:
        with app.database() as connection:
            name = connection.execute(
                "SELECT name FROM schema_migration WHERE version = 72"
            ).fetchone()
            self.assertEqual(name[0], "character_trait_history")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'character_trait_history'"
            ).fetchone()
            self.assertIsNotNone(exists)

    def test_apply_fills_empty_pending_occupied_always_history(self) -> None:
        project_id, scene_id = self._make_project_scene()
        status, seoyun = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "서윤"})
        self.assertEqual(status, 201, seoyun)
        status, jieun = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "지은"})
        self.assertEqual(status, 201, jieun)
        with app.database() as connection:
            connection.execute(
                "UPDATE character SET profile_md = '이미 적어 둔 설정', "
                "short_description = '기존 한 줄', author_notes_md = '작가 메모' "
                "WHERE id = ?",
                (seoyun["id"],),
            )
            connection.execute(
                "INSERT INTO character_alias(character_id, project_id, alias, alias_type) "
                "VALUES (?, ?, '여우', 'other')",
                (seoyun["id"], project_id),
            )
            appearing = character_scene_traits.list_appearing_characters(
                connection, project_id, SCENE_TEXT
            )
            ids = {int(item["id"]) for item in appearing}
            self.assertIn(int(seoyun["id"]), ids)
            self.assertIn(int(jieun["id"]), ids)
            parsed = [
                {
                    "id": int(seoyun["id"]),
                    "name": "서윤",
                    "fields": {
                        "profile_md": "검은 머리, 차분하다",
                        "aliases": ["검은 여우"],
                        "strengths_md": "",
                        "weaknesses_md": "",
                    },
                },
                {
                    "id": int(jieun["id"]),
                    "name": "지은",
                    "fields": {
                        "profile_md": "[성격]\n원래 왼손잡이다",
                        "aliases": [],
                        "strengths_md": "",
                        "weaknesses_md": "",
                    },
                },
            ]
            summaries = character_scene_traits.apply_trait_detections(
                connection,
                project_id=project_id,
                scene_id=scene_id,
                appearing=appearing,
                parsed=parsed,
            )
        by_id = {int(item["id"]): item for item in summaries}
        self.assertEqual(by_id[int(seoyun["id"])]["pending"], 2)
        self.assertEqual(by_id[int(seoyun["id"])]["filled"], 0)
        self.assertEqual(by_id[int(jieun["id"])]["filled"], 1)
        self.assertEqual(by_id[int(jieun["id"])]["pending"], 0)
        status, seoyun_detail = self.request("GET", f"/api/characters/{seoyun['id']}")
        self.assertEqual(status, 200, seoyun_detail)
        self.assertEqual(seoyun_detail["character"]["profile_md"], "이미 적어 둔 설정")
        self.assertEqual(seoyun_detail["character"]["short_description"], "기존 한 줄")
        self.assertEqual(seoyun_detail["character"]["author_notes_md"], "작가 메모")
        self.assertIn("profile_md", seoyun_detail["character"]["tori_analysis"])
        self.assertIn("aliases", seoyun_detail["character"]["tori_analysis"])
        status, jieun_detail = self.request("GET", f"/api/characters/{jieun['id']}")
        self.assertEqual(status, 200, jieun_detail)
        self.assertTrue(jieun_detail["character"]["profile_md"].startswith("〔토리〕"))
        self.assertIn("왼손잡이", jieun_detail["character"]["profile_md"])
        self.assertEqual(jieun_detail["character"].get("tori_analysis") or {}, {})
        with app.database() as connection:
            history = connection.execute(
                "SELECT character_id, field_name FROM character_trait_history "
                "WHERE scene_id = ? ORDER BY id",
                (scene_id,),
            ).fetchall()
            pairs = {(int(row["character_id"]), row["field_name"]) for row in history}
            self.assertIn((int(seoyun["id"]), "profile_md"), pairs)
            self.assertIn((int(seoyun["id"]), "aliases"), pairs)
            self.assertIn((int(jieun["id"]), "profile_md"), pairs)
            notes = connection.execute(
                "SELECT short_description, author_notes_md FROM character WHERE id = ?",
                (seoyun["id"],),
            ).fetchone()
            self.assertEqual(notes["short_description"], "기존 한 줄")
            self.assertEqual(notes["author_notes_md"], "작가 메모")

    def test_mentioned_and_unregistered_are_excluded(self) -> None:
        project_id, _scene_id = self._make_project_scene()
        status, seoyun = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "서윤"})
        self.assertEqual(status, 201, seoyun)
        status, haein = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "해인"})
        self.assertEqual(status, 201, haein)
        before = 0
        with app.database() as connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM character WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()[0]
            appearing = character_scene_traits.list_appearing_characters(
                connection, project_id, SCENE_TEXT
            )
        names = {item["name"] for item in appearing}
        self.assertIn("서윤", names)
        self.assertNotIn("해인", names)
        self.assertNotIn("민재", names)
        parsed = character_scene_traits.parse_trait_json(
            json.dumps({
                "characters": [
                    {"name": "민재", "appearance": "키 크다"},
                    {"id": haein["id"], "name": "해인", "personality": "기억 속에만"},
                ]
            }),
            appearing,
        )
        self.assertEqual(parsed, [])
        with app.database() as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM character WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()[0]
        self.assertEqual(after, before)

    def test_apply_aliases_endpoint(self) -> None:
        project_id, _scene_id = self._make_project_scene()
        status, seoyun = self.request(
            "POST", f"/api/projects/{project_id}/characters", {"name": "서윤"}
        )
        self.assertEqual(status, 201, seoyun)
        with app.database() as connection:
            connection.execute(
                "INSERT INTO character_alias(character_id, project_id, alias, alias_type) "
                "VALUES (?, ?, '여우', 'other')",
                (seoyun["id"], project_id),
            )
            character_import_analysis._upsert_pending(
                connection, int(seoyun["id"]), "aliases", "〔토리〕 검은 여우"
            )
        status, applied = self.request(
            "POST",
            f"/api/characters/{seoyun['id']}/tori-analysis/apply",
            {"field_name": "aliases"},
        )
        self.assertEqual(status, 200, applied)
        aliases = {item["alias"] for item in applied["aliases"]}
        self.assertIn("여우", aliases)
        self.assertIn("검은 여우", aliases)
        self.assertNotIn("aliases", applied["character"].get("tori_analysis") or {})

    @patch.object(gemini_client, "is_configured", return_value=True)
    def test_complete_schedules_analysis_and_fills(self, _configured) -> None:
        project_id, scene_id = self._make_project_scene()
        status, seoyun = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "서윤"})
        self.assertEqual(status, 201, seoyun)
        status, jieun = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "지은"})
        self.assertEqual(status, 201, jieun)
        payload = {
            "characters": [
                {
                    "id": int(seoyun["id"]),
                    "name": "서윤",
                    "appearance": "검은 머리",
                    "personality": "차분하다",
                    "relations": "",
                    "aliases": ["여우"],
                    "strengths_md": "",
                    "weaknesses_md": "",
                    "short_description": "건드리면 안 됨",
                },
                {
                    "id": int(jieun["id"]),
                    "name": "지은",
                    "appearance": "",
                    "personality": "원래 왼손잡이다",
                    "aliases": [],
                },
                {"name": "민재", "appearance": "키 크다"},
            ]
        }

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            blob = f"{system or ''}\n{prompt}"
            if "지속적 특성" in blob or "등록된 등장 인물" in blob:
                return json.dumps(payload, ensure_ascii=False)
            return "댓글"

        with patch.object(gemini_client, "generate_text", side_effect=_fake):
            self._save_scene(scene_id, status="draft", content=SCENE_TEXT)
            self._save_scene(scene_id, status="complete", content=SCENE_TEXT)
            job = self._wait_trait(scene_id)
        self.assertEqual(job["status"], "done")
        names = {item["name"]: item for item in job["characters"]}
        self.assertIn("서윤", names)
        self.assertIn("지은", names)
        self.assertNotIn("민재", names)
        self.assertEqual(names["서윤"]["count"], 2)
        self.assertEqual(names["지은"]["count"], 1)
        status, seoyun_detail = self.request("GET", f"/api/characters/{seoyun['id']}")
        self.assertTrue(seoyun_detail["character"]["profile_md"].startswith("〔토리〕"))
        self.assertEqual(seoyun_detail["character"]["short_description"], "")
        aliases = {item["alias"] for item in seoyun_detail["aliases"]}
        self.assertIn("여우", aliases)
        with app.database() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM character WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()[0]
            history = connection.execute(
                "SELECT COUNT(*) FROM character_trait_history WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()[0]
        self.assertEqual(count, 2)
        self.assertGreaterEqual(history, 3)

    @patch.object(gemini_client, "is_configured", return_value=False)
    def test_complete_skips_without_gemini(self, _configured) -> None:
        project_id, scene_id = self._make_project_scene()
        status, seoyun = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "서윤"})
        self.assertEqual(status, 201, seoyun)
        self._save_scene(scene_id, status="draft", content=SCENE_TEXT)
        self._save_scene(scene_id, status="complete", content=SCENE_TEXT)
        job = self._wait_trait(scene_id)
        self.assertEqual(job["status"], "skipped")
        self.assertEqual(job.get("characters") or [], [])
        self.assertEqual(job.get("error"), "gemini_not_configured")
        status, detail = self.request("GET", f"/api/characters/{seoyun['id']}")
        self.assertEqual(detail["character"]["profile_md"], "")


if __name__ == "__main__":
    unittest.main()
