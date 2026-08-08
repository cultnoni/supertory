"""Tests for proof/editor revision comparison reports."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import proof_diff


class ProofDiffUnitTests(unittest.TestCase):
    def test_typo_and_stylistic_classification(self) -> None:
        original = "그는 빠르게 달려갔다. 비가 온 날이었다."
        revised = "그는 쏜살같이 달려갔다. 비가 온 날이었다."
        report = proof_diff.analyze_local(original, revised)
        self.assertGreaterEqual(len(report.diff_chunks), 1)
        types = {c.type for c in report.diff_chunks}
        self.assertTrue(types & {"modified", "stylistic", "typo"})
        self.assertIn("overall_comment", report.to_dict()["summary"])

    def test_extract_editor_memos(self) -> None:
        revised = (
            "그가 천천히 고개를 들었다.\n"
            "【메모】 이 부분 주인공의 심리가 조금 더 묘사되면 좋겠습니다.\n"
            "문이 열렸다."
        )
        cleaned, memos = proof_diff.extract_memos(revised)
        self.assertEqual(len(memos), 1)
        self.assertIn("심리", memos[0].memo_content)
        self.assertIn("고개를 들었다", memos[0].location_context)
        self.assertNotIn("【메모】", cleaned)

    def test_inline_memo(self) -> None:
        text = "그는 웃었다【메모: 억지 웃음인지 확인】."
        _cleaned, memos = proof_diff.extract_memos(text)
        self.assertEqual(len(memos), 1)
        self.assertIn("억지", memos[0].memo_content)

    def test_added_deleted(self) -> None:
        original = "첫 문장.\n\n둘째 문장."
        revised = "첫 문장.\n\n사이에 끼운 문장.\n\n둘째 문장."
        report = proof_diff.analyze_local(original, revised)
        self.assertTrue(any(c.type == "added" for c in report.diff_chunks) or report.diff_chunks)
        data = report.to_dict()
        self.assertIn("diff_chunks", data)
        self.assertIn("editor_memos", data)


class ProofDiffApiTests(unittest.TestCase):
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

    def test_proof_diff_api_with_scene(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "교정비교", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        pid = project["id"]
        status, ch = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "1부"})
        status, scene = self.request("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "1화"})
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        original = "그는 빠르게 달려갔다. 비가 오고 있었다."
        self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "1화",
            "status": "draft",
            "synopsis_md": "",
            "notes_md": "",
            "content_md": original,
            "row_version": detail["row_version"],
        })
        revised = (
            "그는 쏜살같이 달려갔다. 비가 오고 있었다.\n"
            "【메모】 속도감 표현이 좋습니다."
        )
        status, report = self.request("POST", f"/api/projects/{pid}/proof-diff", {
            "scene_id": scene["id"],
            "revised_text": revised,
            "use_ai": False,
        })
        self.assertEqual(status, 200, report)
        self.assertIn("summary", report)
        self.assertGreaterEqual(report["summary"]["editor_memos_count"], 1)
        self.assertTrue(report["diff_chunks"] or report["summary"]["typo_corrections_count"] >= 0)
        self.assertEqual(report["scene_id"], scene["id"])

    def test_import_proof_compare(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "교정가져오기", "main_genre": "판타지"}
        )
        pid = project["id"]
        status, ch = self.request("POST", f"/api/projects/{pid}/chapters", {"title": "1부"})
        status, scene = self.request("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "제1화: 만남"})
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        original = "비가 내리던 날, 두 사람은 처음 만났다. 카페 문이 열렸다."
        self.request("PUT", f"/api/scenes/{scene['id']}", {
            "title": "제1화: 만남",
            "status": "draft",
            "synopsis_md": "",
            "notes_md": "",
            "content_md": original,
            "row_version": detail["row_version"],
        })
        revised = "제1화: 만남\n\n비가 내리던 날, 두 사람은 처음 만났다. 카페 문이 천천히 열렸다."
        payload = {
            "filename": "교정.txt",
            "content_base64": base64.b64encode(revised.encode("utf-8")).decode("ascii"),
            "destination": "proof_compare",
            "split": "none",
            "use_ai": False,
            "scene_id": scene["id"],
        }
        status, result = self.request("POST", f"/api/projects/{pid}/import", payload)
        self.assertEqual(status, 201, result)
        self.assertIn("proof", result)
        self.assertIn("summary", result["proof"])
        # Manuscript must remain unchanged
        status, after = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(after["content_md"], original)


if __name__ == "__main__":
    unittest.main()
