# -*- coding: utf-8 -*-
"""Previous-segment English context for paragraph translation."""

from __future__ import annotations

import sqlite3
import unittest

import translation_context


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE translation_segments (
            id INTEGER PRIMARY KEY,
            translation_job_id INTEGER NOT NULL,
            chapter_number INTEGER NOT NULL,
            segment_order INTEGER NOT NULL,
            source_text TEXT NOT NULL,
            translated_text TEXT,
            polish_text TEXT
        )
        """
    )
    return connection


def _insert(
    connection: sqlite3.Connection,
    job_id: int,
    chapter: int,
    order: int,
    source: str,
    translated: str | None = None,
    polish: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO translation_segments("
        "translation_job_id, chapter_number, segment_order, source_text, "
        "translated_text, polish_text) VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, chapter, order, source, translated, polish),
    )


class PreviousTranslatedContextTests(unittest.TestCase):
    def test_empty_returns_placeholder(self) -> None:
        self.assertEqual(
            translation_context.format_previous_translated_context([]),
            translation_context.EMPTY_PREVIOUS_CONTEXT,
        )
        connection = _connect()
        try:
            self.assertEqual(
                translation_context.load_previous_translated_context(connection, 1),
                translation_context.EMPTY_PREVIOUS_CONTEXT,
            )
        finally:
            connection.close()

    def test_joins_last_three_in_reading_order(self) -> None:
        connection = _connect()
        try:
            _insert(connection, 1, 1, 0, "원문1", "One.")
            _insert(connection, 1, 1, 1, "원문2", "Two.")
            _insert(connection, 1, 1, 2, "원문3", "Three.")
            _insert(connection, 1, 1, 3, "원문4", "Four.")
            text = translation_context.load_previous_translated_context(connection, 1)
            self.assertEqual(text, "Two.\n\nThree.\n\nFour.")
            self.assertNotIn("원문", text)
        finally:
            connection.close()

    def test_prefers_polish_over_translated(self) -> None:
        connection = _connect()
        try:
            _insert(connection, 1, 1, 0, "원문", "Draft English.", "Polished English.")
            text = translation_context.load_previous_translated_context(connection, 1)
            self.assertEqual(text, "Polished English.")
            self.assertNotIn("Draft English.", text)
            self.assertNotIn("원문", text)
        finally:
            connection.close()

    def test_skips_korean_only_rows(self) -> None:
        connection = _connect()
        try:
            _insert(connection, 1, 1, 0, "한국어만", None, None)
            _insert(connection, 1, 1, 1, "원문", "English.")
            text = translation_context.load_previous_translated_context(connection, 1)
            self.assertEqual(text, "English.")
            self.assertNotIn("한국어만", text)
        finally:
            connection.close()

    def test_cursor_excludes_current_and_later_segments(self) -> None:
        connection = _connect()
        try:
            _insert(connection, 1, 1, 0, "a", "A.")
            _insert(connection, 1, 1, 1, "b", "B.")
            _insert(connection, 1, 1, 2, "c", "C.")
            text = translation_context.load_previous_translated_context(
                connection,
                1,
                before_chapter_number=1,
                before_segment_order=2,
            )
            self.assertEqual(text, "A.\n\nB.")
            self.assertNotIn("C.", text)
        finally:
            connection.close()

    def test_isolates_jobs(self) -> None:
        connection = _connect()
        try:
            _insert(connection, 1, 1, 0, "원문", "Job one.")
            _insert(connection, 2, 1, 0, "원문", "Job two.")
            self.assertEqual(
                translation_context.load_previous_translated_context(connection, 1),
                "Job one.",
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
