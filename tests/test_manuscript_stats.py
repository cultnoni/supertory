"""Manuscript character counts must not inflate from leftover HTML tags."""

from __future__ import annotations

import unittest

import app


class PlainTextFromContentTests(unittest.TestCase):
    def test_opening_p_tags_do_not_add_spaces(self) -> None:
        html = "".join(f"<p>문단{i}</p>" for i in range(1, 16))
        plain = app.plain_text_from_content(html)
        self.assertNotIn(" 문단", plain)
        self.assertFalse(plain.startswith(" "))
        for i in range(1, 16):
            self.assertIn(f"문단{i}", plain)

    def test_stats_ignore_markup(self) -> None:
        html = "<p>안녕</p><p><em>세계</em></p>"
        plain = app.plain_text_from_content(html)
        stats = app.compute_text_stats(plain)
        self.assertEqual(plain, "안녕\n\n세계")
        self.assertEqual(stats["chars_with_space"], len("안녕\n\n세계"))
        self.assertEqual(stats["chars_no_space"], 4)
        self.assertEqual(stats["letters"], 4)


if __name__ == "__main__":
    unittest.main()
