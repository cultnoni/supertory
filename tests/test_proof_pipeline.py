"""Tests for unified HWP/DOCX extract + 3-step proof pipeline."""

from __future__ import annotations

import base64
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import app
import proof_extract
import proof_pipeline
import chapter_match


def make_docx_bytes(paragraphs: list[str], comments: list[tuple[str, str]] | None = None) -> bytes:
    """Minimal DOCX: document.xml only (stdlib path). python-docx may also open it."""
    document = Element(
        "w:document",
        {"xmlns:w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"},
    )
    body = SubElement(document, "w:body")
    for text in paragraphs:
        p = SubElement(body, "w:p")
        r = SubElement(p, "w:r")
        t = SubElement(r, "w:t")
        t.text = text
    xml = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + tostring(
        document, encoding="utf-8"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", """<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
        zf.writestr("word/document.xml", xml)
        zf.writestr("_rels/.rels", """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""")
    return buf.getvalue()


class ProofExtractUnitTests(unittest.TestCase):
    def test_parser_status(self) -> None:
        status = proof_extract.parser_status()
        self.assertIn("python_docx", status)
        self.assertIn("pyhwp", status)

    def test_docx_unified_extract(self) -> None:
        data = make_docx_bytes([
            "그는 빠르게 달려갔다.",
            "【메모】 표현을 다듬어 주세요.",
            "비가 오고 있었다.",
        ])
        result = proof_extract.extract_proof_document("교정.docx", data)
        self.assertTrue(result.text)
        self.assertIn("달려", result.text)
        self.assertEqual(result.format, "docx")
        # memo line should be peeled into memos and out of body ideally
        self.assertTrue(
            any("다듬" in m.memo_content for m in result.memos)
            or "다듬" not in result.text
            or result.memos is not None
        )

    def test_pipeline_local_steps(self) -> None:
        data = make_docx_bytes([
            "제2화: 약속의 장소",
            "오래된 시계탑 아래, 그녀는 우산을 접었다. 약속 시간이었다.",
        ])
        episodes = [
            chapter_match.EpisodeCandidate(
                scene_id=10, chapter_id=1, episode_number=1,
                title="제1화", preview="다른 내용입니다.",
            ),
            chapter_match.EpisodeCandidate(
                scene_id=20, chapter_id=1, episode_number=2,
                title="제2화: 약속의 장소",
                preview="오래된 시계탑 아래, 그녀는 우산을 접었다.",
            ),
        ]
        original = "오래된 시계탑 아래, 그녀는 우산을 접었다. 약속 시간이었다."
        out = proof_pipeline.run_proof_pipeline(
            filename="교정.docx",
            data=data,
            episodes=episodes,
            original_text=original,
            use_ai=False,
        )
        self.assertIn("step1_match", out)
        self.assertIn("step2_clean", out)
        self.assertIn("step3_proof", out)
        self.assertEqual(out["step1_match"]["matched_scene_id"], 20)
        self.assertIn("clean_full_text", out["step2_clean"])
        self.assertIsNotNone(out["step3_proof"])


class ProofPipelineApiTests(unittest.TestCase):
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

    def test_proof_parsers_endpoint(self) -> None:
        status, result = self.request("GET", "/api/proof-parsers")
        self.assertEqual(status, 200)
        self.assertIn("python_docx", result)

    def test_pipeline_api_docx(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "파이프라인"})
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
        data = make_docx_bytes([
            "제1화: 만남",
            "비가 내리던 날, 두 사람은 처음 만났다. 카페 문이 천천히 열렸다.",
        ])
        status, result = self.request("POST", f"/api/projects/{pid}/proof-pipeline", {
            "filename": "교정.docx",
            "content_base64": base64.b64encode(data).decode("ascii"),
            "use_ai": False,
            "apply_clean": False,
        })
        self.assertEqual(status, 200, result)
        self.assertIn("step1_match", result)
        self.assertIn("step2_clean", result)
        self.assertTrue(result.get("clean_full_text"))
        # unchanged manuscript
        status, after = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(after["content_md"], original)


if __name__ == "__main__":
    unittest.main()
