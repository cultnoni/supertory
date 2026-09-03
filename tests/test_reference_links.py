"""Scene reference link vs file classification."""

from __future__ import annotations

import unittest

import app


class ReferenceItemKindTests(unittest.TestCase):
    def test_explicit_link_wins_even_with_source_id(self) -> None:
        self.assertEqual(
            app.reference_item_kind({
                "kind": "link",
                "sourceId": "src-abc",
                "url": "https://example.com/a",
            }),
            "link",
        )

    def test_explicit_file_wins_when_filename_exists(self) -> None:
        self.assertEqual(
            app.reference_item_kind({
                "kind": "file",
                "sourceId": "src-file",
                "fileName": "notes.docx",
            }),
            "file",
        )

    def test_source_id_alone_is_not_a_file(self) -> None:
        self.assertEqual(
            app.reference_item_kind({
                "sourceId": "src-abc",
                "url": "https://example.com/a",
            }),
            "link",
        )

    def test_filename_infers_file_when_kind_is_empty(self) -> None:
        self.assertEqual(
            app.reference_item_kind({"fileName": "a.pdf"}),
            "file",
        )

    def test_misstored_file_with_url_and_no_filename_is_link(self) -> None:
        self.assertEqual(
            app.reference_item_kind({
                "kind": "file",
                "title": "블로그",
                "url": "https://m.blog.naver.com/sshhllee/221219380365",
                "sourceId": "src-mtktn9vo-toth",
                "fileName": "",
            }),
            "link",
        )


class ParseReferenceLinksTests(unittest.TestCase):
    def test_link_keeps_source_id(self) -> None:
        out = app.parse_reference_links([{
            "kind": "link",
            "title": "블로그",
            "url": "https://m.blog.naver.com/x",
            "sourceId": "src-abc",
        }])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "link")
        self.assertEqual(out[0]["sourceId"], "src-abc")
        self.assertEqual(out[0]["url"], "https://m.blog.naver.com/x")

    def test_source_id_without_kind_stays_link(self) -> None:
        out = app.parse_reference_links([{
            "title": "자료",
            "url": "https://example.com/a",
            "sourceId": "src-abc",
        }])
        self.assertEqual(out[0]["kind"], "link")
        self.assertEqual(out[0]["sourceId"], "src-abc")

    def test_heals_file_rows_that_are_really_urls(self) -> None:
        out = app.parse_reference_links([{
            "id": "link-1",
            "kind": "file",
            "title": "블로그",
            "url": "https://m.blog.naver.com/x",
            "sourceId": "src-abc",
            "fileName": "",
        }])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "link")
        self.assertEqual(out[0]["url"], "https://m.blog.naver.com/x")
        self.assertEqual(out[0]["sourceId"], "src-abc")
        self.assertNotIn("fileName", out[0])

    def test_real_file_is_unchanged(self) -> None:
        out = app.parse_reference_links([{
            "kind": "file",
            "title": "자료",
            "sourceId": "src-file",
            "fileName": "notes.docx",
            "fileExt": ".docx",
        }])
        self.assertEqual(out[0]["kind"], "file")
        self.assertEqual(out[0]["fileName"], "notes.docx")
        self.assertEqual(out[0]["sourceId"], "src-file")
