"""Tests for document text extraction and import API."""

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
import document_import


def make_docx(paragraphs: list[str]) -> bytes:
    document = Element("w:document", {"xmlns:w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})
    body = SubElement(document, "w:body")
    for text in paragraphs:
        paragraph = SubElement(body, "w:p")
        run = SubElement(paragraph, "w:r")
        node = SubElement(run, "w:t")
        node.text = text
    xml = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + tostring(document, encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def make_hwpx(paragraphs: list[str]) -> bytes:
    section = Element("hs:sec", {
        "xmlns:hs": "http://www.hancom.co.kr/hwpml/2011/section",
        "xmlns:hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    })
    for text in paragraphs:
        paragraph = SubElement(section, "hp:p")
        run = SubElement(paragraph, "hp:run")
        node = SubElement(run, "hp:t")
        node.text = text
    xml = b'<?xml version="1.0" encoding="UTF-8"?>' + tostring(section, encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", xml)
    return buffer.getvalue()


class DocumentImportUnitTests(unittest.TestCase):
    def test_plain_text_and_korean_encoding(self) -> None:
        extracted = document_import.extract_document("메모.txt", "첫 문장입니다.\n\n둘째 문장.".encode("cp949"))
        self.assertIn("첫 문장입니다.", extracted.text)
        self.assertEqual(extracted.format_name, "txt")

    def test_docx_extraction(self) -> None:
        data = make_docx(["비가 내렸다.", "문이 열렸다."])
        extracted = document_import.extract_document("소설.docx", data)
        self.assertEqual(extracted.text, "비가 내렸다.\n\n문이 열렸다.")

    def test_hwpx_extraction(self) -> None:
        data = make_hwpx(["한강 위로 안개가 끼었다.", "배가 천천히 떠났다."])
        extracted = document_import.extract_document("원고.hwpx", data)
        self.assertIn("한강 위로 안개가 끼었다.", extracted.text)
        self.assertIn("배가 천천히 떠났다.", extracted.text)

    def test_hwp_is_rejected_with_hint(self) -> None:
        with self.assertRaises(ValueError) as context:
            document_import.extract_document("옛파일.hwp", b"not-a-real-hwp")
        self.assertIn("HWPX", str(context.exception))

    def test_split_by_headings(self) -> None:
        text = "# 서장\n시작.\n\n# 본장\n가운데.\n\n제2장 결말\n끝."
        sections = document_import.split_into_sections(text, "headings", "전체")
        titles = [section.title for section in sections]
        self.assertIn("서장", titles)
        self.assertIn("본장", titles)
        self.assertTrue(any("결말" in title or "제2장" in title for title in titles))

    def test_split_by_toc_creates_chapters(self) -> None:
        text = """
목차

서문 ············· 1
제1장 만남 ······· 3
제2장 이별 ······· 10

서문

비가 내렸다.

제1장 만남

두 사람이 카페에서 만났다.

제2장 이별

기차가 떠났다.
""".strip()
        plan = document_import.build_import_plan(text, "toc", "원고")
        titles = [chapter.title for chapter in plan.chapters]
        self.assertEqual(titles, ["서문", "제1장 만남", "제2장 이별"])
        self.assertIn("비가 내렸다.", plan.chapters[0].scenes[0].content)
        self.assertIn("카페", plan.chapters[1].scenes[0].content)

    def test_split_by_toc_nested_scenes(self) -> None:
        text = """
목차
제1장 시작
  1. 아침
  2. 저녁
제2장 끝

제1장 시작

도입부.

1. 아침

해가 떴다.

2. 저녁

달이 떴다.

제2장 끝

끝났다.
""".strip()
        plan = document_import.build_import_plan(text, "toc", "원고")
        self.assertEqual(len(plan.chapters), 2)
        first = plan.chapters[0]
        self.assertEqual(first.title, "제1장 시작")
        scene_titles = [scene.title for scene in first.scenes]
        self.assertIn("1. 아침", scene_titles)
        self.assertIn("2. 저녁", scene_titles)

    def test_purpose_normalisation(self) -> None:
        self.assertEqual(document_import.normalise_purpose("essay"), "essay")
        self.assertEqual(document_import.normalise_purpose("논문"), "paper")
        self.assertEqual(document_import.normalise_purpose("정보 전달"), "nonfiction")


class DocumentImportApiTests(unittest.TestCase):
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

    def test_import_creates_project_chapter_and_scene(self) -> None:
        content = "봄비가 창을 두드렸다.\n\n주인공은 편지를 펼쳤다."
        payload = {
            "filename": "봄비.txt",
            "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "destination": "new_project",
            "split": "none",
            "project_title": "봄비 이야기",
            "purpose": "essay",
        }
        status, result = self.request("POST", "/api/import", payload)
        self.assertEqual(status, 201)
        self.assertTrue(result["created_project"])
        self.assertEqual(result["section_count"], 1)
        self.assertEqual(result["format"], "txt")
        self.assertEqual(result["purpose"], "essay")

        status, scene = self.request("GET", f"/api/scenes/{result['scene_ids'][0]}")
        self.assertEqual(status, 200)
        self.assertIn("봄비가 창을 두드렸다.", scene["content_md"])
        self.assertEqual(scene["status"], "draft")

        status, projects = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual(projects[0]["purpose"], "essay")

    def test_import_by_toc_makes_multiple_chapters(self) -> None:
        content = """
목차
서문
본론
결론

서문
여기는 서문.

본론
여기는 본론.

결론
여기는 결론.
""".strip()
        payload = {
            "filename": "논문초고.txt",
            "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "destination": "new_project",
            "split": "toc",
            "project_title": "짧은 논문",
            "purpose": "paper",
        }
        status, result = self.request("POST", "/api/import", payload)
        self.assertEqual(status, 201)
        self.assertEqual(result["chapter_count"], 3)
        self.assertEqual(result["section_count"], 3)
        self.assertEqual(result["purpose"], "paper")

        status, outline = self.request("GET", f"/api/projects/{result['project_id']}/outline")
        self.assertEqual(status, 200)
        titles = [chapter["title"] for chapter in outline["chapters"]]
        self.assertEqual(titles, ["서문", "본론", "결론"])

    def test_import_docx_into_existing_project(self) -> None:
        status, project = self.request("POST", "/api/projects", {"title": "기존 소설"})
        self.assertEqual(status, 201)
        data = make_docx(["첫 문단", "둘째 문단"])
        payload = {
            "filename": "초고.docx",
            "content_base64": base64.b64encode(data).decode("ascii"),
            "destination": "new_chapter",
            "split": "blank_lines",
            "chapter_title": "가져온 초고",
        }
        status, result = self.request("POST", f"/api/projects/{project['id']}/import", payload)
        self.assertEqual(status, 201)
        self.assertEqual(result["section_count"], 2)
        self.assertEqual(len(result["scene_ids"]), 2)

        status, outline = self.request("GET", f"/api/projects/{project['id']}/outline")
        self.assertEqual(status, 200)
        titles = [chapter["title"] for chapter in outline["chapters"]]
        self.assertIn("가져온 초고", titles)


if __name__ == "__main__":
    unittest.main()
