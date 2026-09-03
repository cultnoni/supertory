"""A small end-to-end check for the local SuperTory web app."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app


class SuperToryAppTests(unittest.TestCase):
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

    def test_writer_can_create_and_save_a_story(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "나의 소설", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        project_id = project["id"]

        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "첫 장"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "첫 만남"})
        self.assertEqual(status, 201)

        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        status, _ = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "첫 만남",
            "status": "draft",
            "synopsis_md": "두 사람이 만난다.",
            "notes_md": "비가 온다.",
            "content_md": "비가 내리던 날, 두 사람은 처음 만났다.",
            "row_version": detail["row_version"],
        })
        self.assertEqual(status, 200)

        status, saved_scene = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(saved_scene["revision_no"], 2)
        self.assertEqual(saved_scene["content_md"], "비가 내리던 날, 두 사람은 처음 만났다.")

        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        status, _ = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "첫 만남",
            "status": "draft",
            "synopsis_md": "두 사람이 만난다.",
            "notes_md": "비가 온다.",
            "content_md": "비가 내리던 날, 두 사람은 처음 만났다.",
            "reference_links": [
                {"title": "날씨 자료", "url": "https://example.com/rain"},
                {"title": "", "url": "example.com/cafe"},
            ],
            "row_version": detail["row_version"],
        })
        self.assertEqual(status, 200)
        status, with_links = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(len(with_links["reference_links"]), 2)
        self.assertEqual(with_links["reference_links"][0]["title"], "날씨 자료")
        self.assertEqual(with_links["reference_links"][0]["kind"], "link")
        self.assertTrue(with_links["reference_links"][1]["url"].startswith("https://"))
        self.assertEqual(with_links["reference_links"][1]["kind"], "link")

        status, detail_for_source = self.request("GET", f"/api/scenes/{scene['id']}")
        status, _saved_with_source = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "첫 만남",
            "status": "draft",
            "synopsis_md": "두 사람이 만난다.",
            "notes_md": "비가 온다.",
            "content_md": "비가 내리던 날, 두 사람은 처음 만났다.",
            "reference_links": [
                {
                    "kind": "file",
                    "title": "블로그",
                    "url": "https://example.com/blog",
                    "sourceId": "src-mirror",
                    "fileName": "",
                },
            ],
            "row_version": detail_for_source["row_version"],
        })
        self.assertEqual(status, 200)
        status, healed = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(healed["reference_links"][0]["kind"], "link")
        self.assertEqual(healed["reference_links"][0]["sourceId"], "src-mirror")
        self.assertEqual(healed["reference_links"][0]["url"], "https://example.com/blog")

        status, detail_for_goal = self.request("GET", f"/api/scenes/{scene['id']}")
        status, _ = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "첫 만남",
            "status": "draft",
            "synopsis_md": "두 사람이 만난다.",
            "notes_md": "비가 온다.",
            "content_md": "비가 내리던 날, 두 사람은 처음 만났다.",
            "reference_links": with_links["reference_links"],
            "goal_word_count": 500,
            "goal_metric": "chars_no_space",
            "row_version": detail_for_goal["row_version"],
        })
        self.assertEqual(status, 200)
        status, with_goal = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(with_goal["goal_word_count"], 500)
        self.assertEqual(with_goal["goal_metric"], "chars_no_space")

        status, goal_only = self.request("PUT", f"/api/scenes/{scene['id']}/goal", {
            "goal_word_count": 1200,
            "goal_metric": "words",
        })
        self.assertEqual(status, 200)
        self.assertEqual(goal_only["goal_word_count"], 1200)
        self.assertEqual(goal_only["goal_metric"], "words")
        status, after_goal = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(after_goal["goal_word_count"], 1200)
        self.assertEqual(after_goal["goal_metric"], "words")

        status, chapter_b = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "둘째 장"})
        self.assertEqual(status, 201)
        status, _ = self.request("PUT", f"/api/chapters/{chapter['id']}", {"title": "첫 장 개명"})
        self.assertEqual(status, 200)
        status, _ = self.request("PUT", f"/api/projects/{project_id}/chapters/reorder", {
            "chapter_ids": [chapter_b["id"], chapter["id"]],
        })
        self.assertEqual(status, 200)
        status, outline = self.request("GET", f"/api/projects/{project_id}/outline")
        self.assertEqual(status, 200)
        self.assertEqual([item["title"] for item in outline["chapters"]], ["둘째 장", "첫 장 개명"])

        status, character = self.request("POST", f"/api/projects/{project_id}/characters", {"name": "서윤"})
        self.assertEqual(status, 201)
        status, _ = self.request("POST", f"/api/characters/{character['id']}/aliases", {"alias": "여우"})
        self.assertEqual(status, 201)
        status, _ = self.request("PUT", f"/api/scenes/{scene['id']}/characters", {
            "character_ids": [character["id"]], "pov_id": character["id"],
        })
        self.assertEqual(status, 200)
        status, members = self.request("GET", f"/api/scenes/{scene['id']}/characters")
        self.assertEqual(status, 200)
        self.assertEqual(members, [{"character_id": character["id"], "appearance_role": "primary", "is_pov": 1}])

    def test_title_only_put_does_not_blank_revision_or_stale_lock(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "제목만 변경 검증", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        status, chapter = self.request("POST", f"/api/projects/{project['id']}/chapters", {"title": "1장"})
        self.assertEqual(status, 201)
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "초고 제목"})
        self.assertEqual(status, 201)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        status, saved = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "초고 제목",
            "status": "draft",
            "content_md": "본문이 있다.",
            "row_version": detail["row_version"],
        })
        self.assertEqual(status, 200)
        after_body = saved["row_version"]
        status, renamed = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "바인더에서 바꾼 제목",
        })
        self.assertEqual(status, 200)
        self.assertGreater(int(renamed["row_version"]), int(after_body))
        self.assertEqual(int(renamed["revision_no"]), 2)
        status, opened = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(opened["title"], "바인더에서 바꾼 제목")
        self.assertEqual(opened["content_md"], "본문이 있다.")
        self.assertEqual(opened["revision_no"], 2)
        status, stale = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "바인더에서 바꾼 제목",
            "status": "draft",
            "content_md": "이어서 타이핑",
            "row_version": after_body,
        })
        self.assertEqual(status, 400)
        status, typed = self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "바인더에서 바꾼 제목",
            "status": "draft",
            "content_md": "이어서 타이핑",
            "row_version": renamed["row_version"],
        })
        self.assertEqual(status, 200)
        status, latest = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(latest["content_md"], "이어서 타이핑")
        self.assertEqual(latest["revision_no"], 3)

    def test_outline_includes_scene_notes_md(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "작가메모 목록", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        pid = project["id"]
        status, chapter = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "1장"})
        self.assertEqual(status, 201)
        status, with_notes = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "메모있음"}
        )
        self.assertEqual(status, 201)
        status, empty = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "메모없음"}
        )
        self.assertEqual(status, 201)

        status, detail = self.request("GET", f"/api/scenes/{with_notes['id']}")
        self.assertEqual(status, 200)
        status, saved = self.request("PUT", f"/api/scenes/{with_notes['id']}", {
            "notes_md": "회차 메모입니다.",
            "row_version": detail["row_version"],
        })
        self.assertEqual(status, 200)
        status, after_notes = self.request("GET", f"/api/scenes/{with_notes['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(after_notes["notes_md"], "회차 메모입니다.")
        self.assertEqual(after_notes.get("content_md") or "", detail.get("content_md") or "")

        status, outline = self.request("GET", f"/api/projects/{pid}/outline")
        self.assertEqual(status, 200)
        scenes = (outline["chapters"][0].get("scenes_flat") or []) if outline.get("chapters") else []
        by_title = {str(item.get("title") or ""): item for item in scenes}
        self.assertIn("메모있음", by_title)
        self.assertIn("메모없음", by_title)
        self.assertEqual(by_title["메모있음"].get("notes_md"), "회차 메모입니다.")
        self.assertEqual(str(by_title["메모없음"].get("notes_md") or "").strip(), "")

        folders = outline.get("folders") or []
        if folders:
            folder_scenes = []
            stack = list(folders)
            while stack:
                node = stack.pop()
                folder_scenes.extend(node.get("scenes") or [])
                stack.extend(node.get("children") or [])
            folder_by_title = {str(item.get("title") or ""): item for item in folder_scenes}
            if "메모있음" in folder_by_title:
                self.assertEqual(folder_by_title["메모있음"].get("notes_md"), "회차 메모입니다.")


if __name__ == "__main__":
    unittest.main()
