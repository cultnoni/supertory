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

    def test_div_blocks_match_editor_innertext_newlines(self) -> None:
        """contenteditable usually stores lines as <div>; innerText uses one newline."""
        html = "".join(f"<div>문단{i}</div>" for i in range(1, 16))
        plain = app.plain_text_from_content(html)
        expected = "\n".join(f"문단{i}" for i in range(1, 16))
        self.assertEqual(plain, expected)
        stats = app.compute_text_stats(plain)
        self.assertEqual(stats["chars_with_space"], len(expected))

    def test_dangling_truncated_tag_is_dropped(self) -> None:
        fragment = '이슬이 맺혔다.<br style="box-sizing: border-box; color: rgb(10, 1'
        plain = app.plain_text_from_content(fragment)
        self.assertEqual(plain, "이슬이 맺혔다.")

    def test_author_note_blocks_are_not_counted(self) -> None:
        html = (
            "<p>안녕</p>"
            '<p data-author-note="1">// 이건 세지 않음</p>'
            "<p>세계</p>"
        )
        plain = app.plain_text_from_content(html)
        self.assertEqual(plain, "안녕\n\n세계")
        stats = app.compute_text_stats(plain)
        self.assertEqual(stats["letters"], 4)
        self.assertNotIn("<", plain)
        self.assertNotIn("box-sizing", plain)


class BinderBodyPreviewTests(unittest.TestCase):
    def test_word_paste_html_does_not_leak_cut_tags(self) -> None:
        html = _word_paste_html("***", "이오나의 마음속은 외로움인지 그리움인지 알 수 없었다.")
        br_at = html.find("<br")
        br_close = html.find(">", br_at)
        self.assertGreater(br_close, 1200)
        preview = app.first_sentence_preview(html)
        self.assertTrue(preview)
        self.assertNotIn("<br", preview)
        self.assertNotIn("style=", preview)
        self.assertNotIn("box-sizing", preview)
        self.assertIn("이오나의 마음속은", preview)

    def test_truncated_html_head_still_strips_dangling_tag(self) -> None:
        html = _word_paste_html("***", "이오나의 마음속은 외로움인지 그리움인지 알 수 없었다.")
        preview = app.first_sentence_preview(html[:1200])
        self.assertNotIn("<br", preview)
        self.assertNotIn("style=", preview)
        self.assertNotIn("box-sizing", preview)

    def test_short_plain_untitled_preview_unchanged(self) -> None:
        self.assertEqual(
            app.first_sentence_preview("테스트88888\n테스트"),
            "테스트88888 테스트",
        )
        self.assertEqual(
            app.outline_body_preview("새 씬", "테스트 제목없음 테스트"),
            "테스트 제목없음 테스트",
        )

    def test_titled_scene_has_empty_preview(self) -> None:
        html = _word_paste_html("", "비가 내리던 날, 두 사람은 처음 만났다.")
        self.assertEqual(app.outline_body_preview("첫 만남", html), "")
        self.assertIn("비가 내리던 날", app.first_sentence_preview(html))


def _word_paste_html(lead: str, sentence: str) -> str:
    """Chrome/Word computed-style dump: the styled <br> closes after 1200 HTML chars."""
    style = (
        "box-sizing: border-box; color: rgb(10, 10, 10); "
        'font-family: Batang, "Apple Myungjo", serif; font-size: medium; '
        "font-style: normal; font-variant-ligatures: normal; "
        "font-variant-caps: normal; font-weight: 400; letter-spacing: normal; "
        "orphans: 2; text-align: start; text-indent: 0px; text-transform: none; "
        "widows: 2; word-spacing: 0px; -webkit-text-stroke-width: 0px; "
        "caret-color: rgb(10, 10, 10); white-space: normal; "
    ) * 4
    return f'<span style="{style}">{lead}</span><br style="{style}">{sentence}'


if __name__ == "__main__":
    unittest.main()
