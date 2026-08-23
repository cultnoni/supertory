"""Continuity helpers for submission-oriented translation jobs.

`previous_context_summary` must be confirmed English (`translated_text` /
`polish_text`), never Korean `source_text`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

PREVIOUS_TRANSLATED_SEGMENT_LIMIT = 3
EMPTY_PREVIOUS_CONTEXT = "(없음, 챕터 첫 문단)"


def format_previous_translated_context(translated_texts: Sequence[object]) -> str:
    """Join already-translated English segments in reading order."""
    pieces = []
    for raw in translated_texts:
        text = "" if raw is None else str(raw).strip()
        if text:
            pieces.append(text)
    if not pieces:
        return EMPTY_PREVIOUS_CONTEXT
    return "\n\n".join(pieces)


def _english_segment_text(polish_text: object, translated_text: object) -> str:
    polish = "" if polish_text is None else str(polish_text).strip()
    if polish:
        return polish
    translated = "" if translated_text is None else str(translated_text).strip()
    return translated


def load_previous_translated_context(
    connection: sqlite3.Connection,
    translation_job_id: int,
    *,
    before_chapter_number: int | None = None,
    before_segment_order: int | None = None,
    limit: int = PREVIOUS_TRANSLATED_SEGMENT_LIMIT,
) -> str:
    """Load the last 2–3 English segments before the current cursor.

    Prefers `polish_text` when present, otherwise `translated_text`.
    Ignores rows that only have Korean `source_text`.
    """
    take = max(1, int(limit or PREVIOUS_TRANSLATED_SEGMENT_LIMIT))
    sql = (
        "SELECT polish_text, translated_text "
        "FROM translation_segments "
        "WHERE translation_job_id = ? "
        "AND ("
        "(translated_text IS NOT NULL AND trim(translated_text) != '') "
        "OR (polish_text IS NOT NULL AND trim(polish_text) != '')"
        ")"
    )
    params: list[object] = [int(translation_job_id)]
    if before_chapter_number is not None and before_segment_order is not None:
        sql += (
            " AND (chapter_number < ? "
            "OR (chapter_number = ? AND segment_order < ?))"
        )
        chapter = int(before_chapter_number)
        params.extend([chapter, chapter, int(before_segment_order)])
    elif before_chapter_number is not None:
        sql += " AND chapter_number < ?"
        params.append(int(before_chapter_number))
    sql += " ORDER BY chapter_number DESC, segment_order DESC LIMIT ?"
    params.append(take)
    rows = connection.execute(sql, params).fetchall()
    english = [_english_segment_text(row[0], row[1]) for row in reversed(rows)]
    return format_previous_translated_context(english)
