"""Straight → curly quote conversion used when a scene becomes complete."""

from __future__ import annotations

import unittest

from typographic_quotes import convert_typographic_quotes


class TypographicQuotesTests(unittest.TestCase):
    def test_double_quotes_pair(self) -> None:
        self.assertEqual(
            convert_typographic_quotes('그는 "안녕" 했다.'),
            "그는 “안녕” 했다.",
        )

    def test_single_quotes_pair(self) -> None:
        self.assertEqual(
            convert_typographic_quotes("그는 '안녕' 했다."),
            "그는 ‘안녕’ 했다.",
        )

    def test_existing_curly_quotes_are_left_alone(self) -> None:
        source = "그는 “안녕” 하고 ‘속삭였다’."
        self.assertEqual(convert_typographic_quotes(source), source)

    def test_idempotent_after_repeat(self) -> None:
        source = '그는 "안녕" 하고 \'속삭였다\'.'
        once = convert_typographic_quotes(source)
        twice = convert_typographic_quotes(once)
        self.assertEqual(once, twice)
        self.assertEqual(once, "그는 “안녕” 하고 ‘속삭였다’.")

    def test_html_tags_and_attributes_untouched(self) -> None:
        source = '<p class="quote">그는 "안녕" <b>했다</b>.</p>'
        self.assertEqual(
            convert_typographic_quotes(source),
            '<p class="quote">그는 “안녕” <b>했다</b>.</p>',
        )

    def test_quotes_span_inline_tags(self) -> None:
        source = '"hello <b>world</b>"'
        self.assertEqual(convert_typographic_quotes(source), "“hello <b>world</b>”")

    def test_apostrophe_in_contraction(self) -> None:
        self.assertEqual(convert_typographic_quotes("don't"), "don’t")

    def test_quot_entity_in_text(self) -> None:
        self.assertEqual(
            convert_typographic_quotes("말 &quot;하나&quot;."),
            "말 “하나”.",
        )

    def test_empty_and_none(self) -> None:
        self.assertEqual(convert_typographic_quotes(""), "")
        self.assertEqual(convert_typographic_quotes(None), "")
