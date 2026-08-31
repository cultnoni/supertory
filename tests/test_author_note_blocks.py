"""Writer-only comment paragraphs must never leak to readers or mix with footnotes."""

from __future__ import annotations

import unittest

import app
import author_note_blocks
import document_export


NOTE_HTML = (
    "<p>본문이 시작된다.</p>"
    '<p data-author-note="1" class="st-author-note">// 여기서 복선을 심어야 함</p>'
    "<p>다음 문단.</p>"
)

FOOTNOTE_HTML = (
    '<p>본문<sup class="fn-ref" data-fn-id="f1">1</sup></p>'
    '<div class="fn-footer"><div class="fn-item" data-fn-id="f1">'
    '<span class="fn-num">1</span><span class="fn-text">독자에게 보이는 각주</span>'
    "</div></div>"
)


class AuthorNoteBlockTests(unittest.TestCase):
    def test_strip_removes_note_keeps_body(self) -> None:
        cleaned = author_note_blocks.strip_author_note_html(NOTE_HTML)
        self.assertNotIn("복선", cleaned)
        self.assertNotIn("data-author-note", cleaned)
        self.assertIn("본문이 시작된다.", cleaned)
        self.assertIn("다음 문단.", cleaned)

    def test_strip_nested_div_note(self) -> None:
        html = (
            '<div data-author-note="1">비밀 <div>안쪽</div> 끝</div>'
            "<p>공개</p>"
        )
        cleaned = author_note_blocks.strip_author_note_html(html)
        self.assertNotIn("비밀", cleaned)
        self.assertNotIn("안쪽", cleaned)
        self.assertIn("공개", cleaned)

    def test_strip_does_not_touch_footnotes(self) -> None:
        cleaned = author_note_blocks.strip_author_note_html(FOOTNOTE_HTML)
        self.assertIn("fn-ref", cleaned)
        self.assertIn("fn-footer", cleaned)
        self.assertIn("독자에게 보이는 각주", cleaned)

    def test_plain_text_excludes_notes_by_default(self) -> None:
        plain = app.plain_text_from_content(NOTE_HTML)
        self.assertIn("본문이 시작된다.", plain)
        self.assertIn("다음 문단.", plain)
        self.assertNotIn("복선", plain)
        self.assertNotIn("//", plain)

    def test_plain_text_can_keep_notes_for_tory(self) -> None:
        plain = app.plain_text_from_content(NOTE_HTML, include_author_notes=True)
        self.assertIn("복선", plain)
        self.assertIn("본문이 시작된다.", plain)

    def test_footnote_plain_text_still_includes_reader_notes(self) -> None:
        plain = app.plain_text_from_content(FOOTNOTE_HTML)
        self.assertIn("독자에게 보이는 각주", plain)
        self.assertIn("본문", plain)

    def test_translation_paragraphs_drop_notes(self) -> None:
        parts = app.split_source_paragraphs(NOTE_HTML)
        joined = "\n".join(parts)
        self.assertNotIn("복선", joined)
        self.assertTrue(any("본문" in part for part in parts))

    def test_txt_export_omits_notes(self) -> None:
        plain = app.plain_text_from_content(NOTE_HTML)
        exported = document_export.export_bytes(
            "txt",
            project_title="주석제외",
            chapters=[
                {
                    "title": "1장",
                    "scenes": [{"title": "1화", "content_plain": plain}],
                }
            ],
        )
        text = exported.data.decode("utf-8")
        self.assertIn("본문이 시작된다.", text)
        self.assertNotIn("복선", text)
        self.assertNotIn("//", text)

    def test_html_export_omits_notes(self) -> None:
        plain = app.plain_text_from_content(NOTE_HTML)
        exported = document_export.export_bytes(
            "html",
            project_title="주석제외",
            chapters=[
                {
                    "title": "1장",
                    "scenes": [{"title": "1화", "content_plain": plain}],
                }
            ],
        )
        html = exported.data.decode("utf-8")
        self.assertIn("본문이 시작된다.", html)
        self.assertNotIn("복선", html)
        self.assertNotIn("data-author-note", html)

    def test_trait_and_item_analysis_use_stripped_plain_text(self) -> None:
        html = (
            "<p>서윤이 문을 열고 들어왔다. 흑염검을 뽑았다.</p>"
            '<p data-author-note="1">// 사실 이 인물은 배신자다 TRAITOR_SPOILER_XYZ</p>'
        )
        stripped = app.plain_text_from_content(html)
        kept = app.plain_text_from_content(html, include_author_notes=True)
        self.assertNotIn("TRAITOR_SPOILER_XYZ", stripped)
        self.assertIn("문을 열고 들어왔다", stripped)
        self.assertIn("흑염검", stripped)
        self.assertIn("TRAITOR_SPOILER_XYZ", kept)


if __name__ == "__main__":
    unittest.main()
