"""Tests for HWP proof text cleaning."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import proof_clean


class ProofCleanUnitTests(unittest.TestCase):
    def test_strips_memo_and_page_junk(self) -> None:
        raw = (
            "　첫 문장입니다.\n"
            "【메모】 심리 묘사 보강 요청\n"
            "\n"
            "두번째 문단입니다.\n"
            "— 12 —\n"
            "\f"
            "세 번째 단락.\n"
            "※ 편집 메모: 삭제해도 됨\n"
        )
        out = proof_clean.clean_proof_text(raw)
        self.assertIn("첫 문장입니다", out)
        self.assertIn("두번째 문단", out)
        self.assertIn("세 번째 단락", out)
        self.assertNotIn("메모", out)
        self.assertNotIn("12", out)
        data = proof_clean.clean_to_dict(raw)
        self.assertEqual(data["clean_full_text"], out)

    def test_revision_markers(self) -> None:
        raw = "그는 {+빠르게+} 달렸다. [-천천히-] 문을 열었다."
        out = proof_clean.clean_proof_text(raw)
        self.assertIn("빠르게", out)
        self.assertNotIn("천천히", out)
        self.assertNotIn("{+", out)

    def test_soft_wrap_cjk(self) -> None:
        raw = "한강 위로 안개가\n끼었다. 배가 천천히 떠났다."
        out = proof_clean.clean_proof_text(raw)
        self.assertIn("안개가끼었다", out.replace(" ", ""))
        self.assertNotIn("\n\n\n", out)

    def test_paragraph_breaks_kept(self) -> None:
        raw = "첫째 단락.\n\n\n\n둘째 단락."
        out = proof_clean.clean_proof_text(raw)
        self.assertEqual(out, "첫째 단락.\n\n둘째 단락.")

    def test_inline_memo(self) -> None:
        raw = "그는 웃었다【메모: 억지인지 확인】. 그리고 걸었다."
        out = proof_clean.clean_proof_text(raw)
        self.assertNotIn("억지", out)
        self.assertIn("그는 웃었다", out)


class ProofCleanApiTests(unittest.TestCase):
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

    def test_proof_clean_api(self) -> None:
        raw = "본문입니다.\n【메모】 확인\n— 3 —\n"
        status, result = self.request("POST", "/api/proof-clean", {"text": raw})
        self.assertEqual(status, 200, result)
        self.assertIn("clean_full_text", result)
        self.assertEqual(result["clean_full_text"].strip(), "본문입니다.")
        self.assertNotIn("메모", result["clean_full_text"])


if __name__ == "__main__":
    unittest.main()
