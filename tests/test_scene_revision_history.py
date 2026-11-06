"""회차 「이전 버전」: 목록·본문·비교·복원·체크포인트 (임시 DB, 실제 HTTP 경로).

원칙: 이력은 지우거나 고치지 않는다. 복원은 새 버전을 만든다.
설계: docs/design-ai-context-and-style-2026-11.md §7-1.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
from services.scene_revision_service import diff_texts


class DiffTextsTests(unittest.TestCase):
    def test_identical_texts(self) -> None:
        result = diff_texts("첫 문단\n둘째 문단", "첫 문단\n\n둘째 문단")
        self.assertTrue(result["stats"]["identical"])
        self.assertEqual([op["type"] for op in result["ops"]], ["equal"])

    def test_insert_and_delete_paragraphs(self) -> None:
        result = diff_texts("하나\n둘\n셋", "하나\n셋\n넷")
        kinds = [op["type"] for op in result["ops"]]
        self.assertIn("delete", kinds)
        self.assertIn("insert", kinds)
        deleted = [line for op in result["ops"] if op["type"] == "delete" for line in op["lines"]]
        inserted = [line for op in result["ops"] if op["type"] == "insert" for line in op["lines"]]
        self.assertEqual(deleted, ["둘"])
        self.assertEqual(inserted, ["넷"])

    def test_changed_paragraph_shows_word_parts(self) -> None:
        result = diff_texts(
            "도윤은 천천히 걸음을 옮겼다.",
            "도윤은 서둘러 걸음을 옮겼다.",
        )
        change = [op for op in result["ops"] if op["type"] == "change"]
        self.assertEqual(len(change), 1)
        parts = change[0]["parts"]
        self.assertIn({"op": "delete", "text": "천천히"}, parts)
        self.assertIn({"op": "insert", "text": "서둘러"}, parts)
        self.assertEqual(result["stats"]["changed_paragraphs"], 1)

    def test_very_different_paragraph_is_delete_plus_insert(self) -> None:
        result = diff_texts("완전히 다른 문장이었다.", "전혀 새로운 줄로 바뀌었음.")
        kinds = [op["type"] for op in result["ops"]]
        self.assertEqual(kinds, ["delete", "insert"])

    def test_empty_texts(self) -> None:
        result = diff_texts("", "")
        self.assertEqual(result["ops"], [])
        self.assertTrue(result["stats"]["identical"])


class SceneRevisionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, project = self.request("POST", "/api/projects", {"title": "이전 버전 검증", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        status, chapter = self.request(
            "POST", f"/api/projects/{project['id']}/chapters", {"title": "1장"}
        )
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"}
        )
        self.scene_id = int(scene["id"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, (json.loads(raw) if raw else {})

    # —— 도구 ——

    def row_version(self) -> int:
        status, detail = self.request("GET", f"/api/scenes/{self.scene_id}")
        self.assertEqual(status, 200, detail)
        return int(detail["row_version"])

    def save(self, html: str, **extra) -> dict:
        payload = {
            "title": "1화",
            "content_md": html,
            "row_version": self.row_version(),
            "save_note": "자동 저장",
            **extra,
        }
        status, saved = self.request("PUT", f"/api/scenes/{self.scene_id}", payload)
        self.assertEqual(status, 200, saved)
        return saved

    def revisions(self) -> dict:
        status, listing = self.request("GET", f"/api/scenes/{self.scene_id}/revisions")
        self.assertEqual(status, 200, listing)
        return listing

    def revision_count(self) -> int:
        with app.database() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM scene_revision WHERE scene_id = ?", (self.scene_id,)
                ).fetchone()[0]
            )

    # —— 목록·본문·비교 ——

    def test_list_is_newest_first_with_word_delta(self) -> None:
        self.save("<p>하나</p>")
        self.save("<p>하나 둘 셋</p>")
        listing = self.revisions()
        rows = listing["revisions"]
        self.assertEqual(listing["total"], len(rows))
        numbers = [row["revision_no"] for row in rows]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        self.assertTrue(rows[0]["is_current"])
        self.assertFalse(any(row["is_current"] for row in rows[1:]))
        self.assertGreater(rows[0]["delta"], 0)

    def test_get_revision_returns_plain_text(self) -> None:
        self.save("<p>첫 버전 문장</p>")
        self.save("<p>둘째 버전</p>")
        first_no = self.revisions()["revisions"][1]["revision_no"]
        status, one = self.request("GET", f"/api/scenes/{self.scene_id}/revisions/{first_no}")
        self.assertEqual(status, 200, one)
        self.assertEqual(one["plain_text"], "첫 버전 문장")
        self.assertFalse(one["is_current"])

    def test_diff_compares_version_with_current(self) -> None:
        self.save("<p>도윤은 천천히 걸었다.</p>")
        self.save("<p>도윤은 서둘러 걸었다.</p>")
        old_no = self.revisions()["revisions"][1]["revision_no"]
        status, diff = self.request(
            "GET", f"/api/scenes/{self.scene_id}/revisions/{old_no}/diff"
        )
        self.assertEqual(status, 200, diff)
        self.assertEqual(diff["from_revision"], old_no)
        self.assertFalse(diff["stats"]["identical"])

    def test_unknown_revision_is_an_error(self) -> None:
        status, body = self.request("GET", f"/api/scenes/{self.scene_id}/revisions/999")
        self.assertEqual(status, 404, body)  # "찾을 수 없습니다"는 404로 돌려준다

    # —— 복원 ——

    def test_restore_makes_a_new_version_and_keeps_history(self) -> None:
        self.save("<p>되살릴 원고</p>")
        self.save("<p>망친 원고</p>")
        listing = self.revisions()
        target_no = listing["revisions"][1]["revision_no"]
        current_no = listing["revisions"][0]["revision_no"]
        before = self.revision_count()

        status, restored = self.request(
            "POST",
            f"/api/scenes/{self.scene_id}/revisions/{target_no}/restore",
            {"row_version": self.row_version()},
        )
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored["restored_from"], target_no)
        self.assertEqual(self.revision_count(), before + 1)  # 지우지 않고 하나 늘어난다

        status, detail = self.request("GET", f"/api/scenes/{self.scene_id}")
        self.assertIn("되살릴 원고", detail["content_md"])

        after = self.revisions()["revisions"]
        self.assertEqual(after[0]["save_note"], f"@restore:{target_no}")
        replaced = next(row for row in after if row["revision_no"] == current_no)
        self.assertIn("before_restore", [mark["reason"] for mark in replaced["marks"]])

    def test_restore_refuses_completed_scene(self) -> None:
        self.save("<p>예전</p>")
        self.save("<p>지금</p>", status="complete")
        target_no = self.revisions()["revisions"][-1]["revision_no"]
        status, body = self.request(
            "POST",
            f"/api/scenes/{self.scene_id}/revisions/{target_no}/restore",
            {"row_version": self.row_version()},
        )
        self.assertEqual(status, 400, body)
        self.assertIn("완성", str(body))

    def test_restore_refuses_stale_row_version(self) -> None:
        self.save("<p>예전</p>")
        self.save("<p>지금</p>")
        target_no = self.revisions()["revisions"][1]["revision_no"]
        status, body = self.request(
            "POST",
            f"/api/scenes/{self.scene_id}/revisions/{target_no}/restore",
            {"row_version": self.row_version() - 1},
        )
        self.assertEqual(status, 400, body)

    def test_restore_of_current_version_is_refused(self) -> None:
        self.save("<p>지금</p>")
        current_no = self.revisions()["revisions"][0]["revision_no"]
        before = self.revision_count()
        status, body = self.request(
            "POST",
            f"/api/scenes/{self.scene_id}/revisions/{current_no}/restore",
            {"row_version": self.row_version()},
        )
        self.assertEqual(status, 400, body)
        self.assertEqual(self.revision_count(), before)

    def test_revisions_stay_immutable(self) -> None:
        """복원·체크포인트 뒤에도 기존 행은 고칠 수 없다(트리거 유지)."""
        self.save("<p>원고</p>")
        with self.assertRaises(Exception):
            with app.database() as connection:
                connection.execute(
                    "UPDATE scene_revision SET save_note = 'x' WHERE scene_id = ?",
                    (self.scene_id,),
                )

    # —— 체크포인트 ——

    def test_manual_checkpoint_mark_and_unmark(self) -> None:
        self.save("<p>표시할 원고</p>")
        status, marked = self.request(
            "POST",
            f"/api/scenes/{self.scene_id}/checkpoint",
            {"label": "  1차   완성본  " + "가" * 80},
        )
        self.assertEqual(status, 200, marked)
        self.assertEqual(marked["marks"][0]["reason"], "manual")
        self.assertLessEqual(len(marked["marks"][0]["label"]), 60)
        self.assertTrue(marked["marks"][0]["label"].startswith("1차 완성본"))

        status, unmarked = self.request(
            "POST",
            f"/api/scenes/{self.scene_id}/checkpoint",
            {"revision_no": marked["revision_no"], "checkpoint": False},
        )
        self.assertEqual(status, 200, unmarked)
        self.assertEqual(unmarked["marks"], [])

    def test_checkpoint_rejects_unknown_reason(self) -> None:
        self.save("<p>원고</p>")
        status, body = self.request(
            "POST", f"/api/scenes/{self.scene_id}/checkpoint", {"reason": "whatever"}
        )
        self.assertEqual(status, 400, body)

    def test_ai_insert_checkpoint_reason_is_accepted(self) -> None:
        self.save("<p>AI 넣기 전</p>")
        status, marked = self.request(
            "POST", f"/api/scenes/{self.scene_id}/checkpoint", {"reason": "before_ai_insert"}
        )
        self.assertEqual(status, 200, marked)
        self.assertEqual(marked["marks"][0]["reason"], "before_ai_insert")

    def test_completing_a_scene_marks_the_version_before(self) -> None:
        self.save('<p>"따옴표" 원고</p>')
        before_no = self.revisions()["revisions"][0]["revision_no"]
        self.save('<p>"따옴표" 원고</p>', status="complete")  # 완성: 따옴표 변환으로 새 버전
        rows = self.revisions()["revisions"]
        self.assertNotEqual(rows[0]["revision_no"], before_no)
        marked = next(row for row in rows if row["revision_no"] == before_no)
        self.assertIn("before_complete", [mark["reason"] for mark in marked["marks"]])

    def test_overwrite_marks_previous_but_not_blank_scene(self) -> None:
        service = app.get_scene_content_service()
        with app.database() as connection:
            service.write_scene_content(connection, self.scene_id, "<p>가져온 원고</p>")
        rows = self.revisions()["revisions"]
        self.assertTrue(all(not row["marks"] for row in rows))  # 빈 회차는 표시하지 않는다

        before_no = rows[0]["revision_no"]
        with app.database() as connection:
            service.write_scene_content(connection, self.scene_id, "<p>교정본으로 덮음</p>")
        rows = self.revisions()["revisions"]
        marked = next(row for row in rows if row["revision_no"] == before_no)
        self.assertIn("before_overwrite", [mark["reason"] for mark in marked["marks"]])


if __name__ == "__main__":
    unittest.main()
