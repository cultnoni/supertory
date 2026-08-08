"""Unit tests for episode / chapter matching (교정고 → 회차)."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import chapter_match


class ChapterMatchUnitTests(unittest.TestCase):
    def test_prefers_same_episode_number_and_title(self) -> None:
        episodes = [
            chapter_match.EpisodeCandidate(
                scene_id=1, chapter_id=10, episode_number=1,
                title="제1화: 시작", preview="비가 내리던 날 두 사람이 만났다.",
            ),
            chapter_match.EpisodeCandidate(
                scene_id=2, chapter_id=10, episode_number=15,
                title="제15화: 약속의 장소", preview="오래된 시계탑 아래, 그녀는 우산을 접었다.",
            ),
            chapter_match.EpisodeCandidate(
                scene_id=3, chapter_id=10, episode_number=16,
                title="제16화: 이별", preview="기차가 떠나고 플랫폼엔 아무도 없었다.",
            ),
        ]
        target = (
            "제15화: 약속의 장소\n\n"
            "오래된 시계탑 아래, 그녀는 우산을 접었다. "
            "빗물이 구두 끝으로 흘러내렸다."
        )
        result = chapter_match.match_local(target, episodes, target_title="제15화: 약속의 장소")
        self.assertEqual(result.matched_scene_id, 2)
        self.assertEqual(result.matched_episode_number, 15)
        self.assertGreaterEqual(result.confidence_score, 0.5)
        self.assertEqual(result.method, "local")

    def test_body_overlap_without_title(self) -> None:
        episodes = [
            chapter_match.EpisodeCandidate(
                scene_id=11, chapter_id=1, episode_number=3,
                title="안개", preview="한강 위로 안개가 끼었다. 배가 천천히 떠났다.",
            ),
            chapter_match.EpisodeCandidate(
                scene_id=12, chapter_id=1, episode_number=4,
                title="시장", preview="사람들로 붐비는 시장 골목.",
            ),
        ]
        target = "한강 위로 안개가 끼었다. 배가 천천히 떠났다. 멀리서 종소리가 들렸다."
        result = chapter_match.match_local(target, episodes)
        self.assertEqual(result.matched_scene_id, 11)
        self.assertGreaterEqual(result.confidence_score, chapter_match.HARD_REJECT_THRESHOLD)

    def test_no_false_match_on_unrelated(self) -> None:
        episodes = [
            chapter_match.EpisodeCandidate(
                scene_id=1, chapter_id=1, episode_number=1,
                title="서장", preview="왕국이 세워진 지 백 년이 지났다.",
            ),
        ]
        result = chapter_match.match_local(
            "오늘 점심으로 김치찌개를 먹었다. 날씨가 좋았다.",
            episodes,
            target_title="일기",
        )
        # May still pick only candidate with low score → method none if below hard reject
        if result.matched_scene_id is not None:
            self.assertLess(result.confidence_score, 0.75)

    def test_extract_episode_number(self) -> None:
        self.assertEqual(chapter_match.extract_episode_number("제15화: 약속"), 15)
        self.assertEqual(chapter_match.extract_episode_number("3화 시작"), 3)
        self.assertIsNone(chapter_match.extract_episode_number("프롤로그"))


class ChapterMatchApiTests(unittest.TestCase):
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
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def _seed_project(self) -> tuple[int, int, int]:
        status, project = self.request(
            "POST", "/api/projects", {"title": "매칭테스트", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        pid = project["id"]
        status, ch = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "1부"})
        self.assertEqual(status, 201)
        status, s1 = self.request("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "제1화: 만남"})
        self.assertEqual(status, 201)
        status, detail = self.request("GET", f"/api/scenes/{s1['id']}")
        self.request("PUT", f"/api/scenes/{s1['id']}", {
            "title": "제1화: 만남",
            "status": "draft",
            "synopsis_md": "",
            "notes_md": "",
            "content_md": "비가 내리던 날, 두 사람은 처음 만났다. 카페 문이 열렸다.",
            "row_version": detail["row_version"],
        })
        status, s2 = self.request("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "제2화: 약속의 장소"})
        self.assertEqual(status, 201)
        status, detail2 = self.request("GET", f"/api/scenes/{s2['id']}")
        self.request("PUT", f"/api/scenes/{s2['id']}", {
            "title": "제2화: 약속의 장소",
            "status": "draft",
            "synopsis_md": "",
            "notes_md": "",
            "content_md": "오래된 시계탑 아래, 그녀는 우산을 접었다. 약속 시간이었다.",
            "row_version": detail2["row_version"],
        })
        return pid, s1["id"], s2["id"]

    def test_match_episode_api(self) -> None:
        pid, _s1, s2 = self._seed_project()
        status, result = self.request("POST", f"/api/projects/{pid}/match-episode", {
            "target_title": "제2화: 약속의 장소",
            "target_text": "오래된 시계탑 아래, 그녀는 우산을 접었다. 약속 시간이었다. 바람이 불었다.",
            "use_ai": False,
        })
        self.assertEqual(status, 200, result)
        self.assertEqual(result["matched_scene_id"], s2)
        self.assertEqual(result["matched_episode_number"], 2)
        self.assertGreaterEqual(result["confidence_score"], 0.4)

    def test_import_match_replace(self) -> None:
        pid, _s1, s2 = self._seed_project()
        text = (
            "제2화: 약속의 장소\n\n"
            "오래된 시계탑 아래, 그녀는 우산을 접었다. 약속 시간이었다. "
            "교정된 문장이 여기 추가되었다."
        )
        payload = {
            "filename": "교정고2화.txt",
            "content_base64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "destination": "match_replace_scene",
            "split": "none",
            "use_ai": False,
            "keep_scene_title": True,
        }
        status, result = self.request("POST", f"/api/projects/{pid}/import", payload)
        self.assertEqual(status, 201, result)
        self.assertEqual(result["scene_ids"], [s2])
        self.assertIn("match", result)
        self.assertEqual(result["match"]["matched_scene_id"], s2)

        status, scene = self.request("GET", f"/api/scenes/{s2}")
        self.assertEqual(status, 200)
        self.assertIn("교정된 문장", scene["content_md"])
        self.assertEqual(scene["title"], "제2화: 약속의 장소")


if __name__ == "__main__":
    unittest.main()
