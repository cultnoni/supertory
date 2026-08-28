"""SQLite persistence for translated segments and chapter polish."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

import translation_context


class TranslationSegmentRepository:
    """Keep first-pass and polish segment SQL behind one repository."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_pending_segments(self, job_id: int, limit: int) -> list[dict]:
        """Return the next ordered pending batch without crossing a chapter."""
        first = self.connection.execute(
            "SELECT chapter_number FROM translation_segments "
            "WHERE translation_job_id = ? "
            "AND trim(COALESCE(translated_text, '')) = '' "
            "ORDER BY chapter_number ASC, segment_order ASC, id ASC LIMIT 1",
            (int(job_id),),
        ).fetchone()
        if first is None:
            return []
        rows = self.connection.execute(
            "SELECT * FROM translation_segments "
            "WHERE translation_job_id = ? AND chapter_number = ? "
            "AND trim(COALESCE(translated_text, '')) = '' "
            "ORDER BY segment_order ASC, id ASC LIMIT ?",
            (int(job_id), int(first["chapter_number"]), max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_translated_batch(
        self,
        job_id: int,
        segment_results: list[dict],
    ) -> int:
        result_ids = [int(result["id"]) for result in segment_results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("번역 배치에 중복 문단 ID가 있습니다.")
        if result_ids:
            placeholders = ",".join("?" * len(result_ids))
            rows = self.connection.execute(
                "SELECT id FROM translation_segments "
                "WHERE translation_job_id = ? "
                f"AND id IN ({placeholders})",
                [int(job_id), *result_ids],
            ).fetchall()
            stored_ids = {int(row["id"]) for row in rows}
            if stored_ids != set(result_ids):
                raise LookupError(
                    "번역 작업에 속하지 않는 문단이 포함되어 있습니다."
                )
        saved = 0
        for result in segment_results:
            translated = str(result.get("translated_text") or "").strip()
            if not translated:
                continue
            notes = result.get("translation_notes") or []
            notes_raw = (
                notes
                if isinstance(notes, str)
                else json.dumps(notes, ensure_ascii=False)
            )
            cursor = self.connection.execute(
                """
                UPDATE translation_segments
                SET translated_text = ?, translation_notes_json = ?,
                    polish_text = NULL, polish_proposal_text = NULL,
                    polish_choice = NULL, is_approved = 0,
                    needs_manual_review = 0, updated_at = datetime('now')
                WHERE translation_job_id = ? AND id = ?
                """,
                (
                    translated,
                    notes_raw,
                    int(job_id),
                    int(result["id"]),
                ),
            )
            saved += int(cursor.rowcount or 0)
        return saved

    def save_translated_segment(
        self,
        job_id: int,
        segment_id: int,
        translated_text: str,
        notes: object,
        *,
        needs_manual_review: bool,
    ) -> dict:
        notes_raw = (
            notes
            if isinstance(notes, str)
            else json.dumps(notes, ensure_ascii=False)
        )
        cursor = self.connection.execute(
            """
            UPDATE translation_segments
            SET translated_text = ?, translation_notes_json = ?,
                polish_text = NULL, polish_proposal_text = NULL,
                polish_choice = NULL, is_approved = 0,
                needs_manual_review = ?, updated_at = datetime('now')
            WHERE translation_job_id = ? AND id = ?
            """,
            (
                str(translated_text),
                notes_raw,
                1 if needs_manual_review else 0,
                int(job_id),
                int(segment_id),
            ),
        )
        if int(cursor.rowcount or 0) < 1:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        return self.get_segment(int(segment_id)) or {}

    def mark_segment_needs_review(self, segment_id: int, note: str) -> None:
        row = self.get_segment(int(segment_id))
        if row is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        raw = row.get("translation_notes_json")
        try:
            notes = json.loads(str(raw)) if raw else []
        except (json.JSONDecodeError, TypeError, ValueError):
            notes = []
        if not isinstance(notes, list):
            notes = [{"note": str(notes)}]
        if str(note).strip():
            notes.append({"note": str(note).strip(), "needs_manual_review": True})
        self.connection.execute(
            "UPDATE translation_segments SET needs_manual_review = 1, "
            "translation_notes_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(notes, ensure_ascii=False), int(segment_id)),
        )

    def save_separator_passthrough(self, segment_id: int) -> dict:
        row = self.get_segment(int(segment_id))
        if row is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        source = str(row.get("source_text") or "")
        self.connection.execute(
            """
            UPDATE translation_segments
            SET translated_text = ?, polish_text = ?,
                translation_notes_json = ?, needs_manual_review = 0,
                is_approved = 1, updated_at = datetime('now')
            WHERE id = ?
            """,
            (source, source, "[]", int(segment_id)),
        )
        return self.get_segment(int(segment_id)) or {}

    def get_segment(self, segment_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_segments WHERE id = ?",
            (int(segment_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_segment_for_job(self, segment_id: int, job_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_segments "
            "WHERE id = ? AND translation_job_id = ?",
            (int(segment_id), int(job_id)),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_segments(
        self,
        job_id: int,
        chapter_number: int | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM translation_segments WHERE translation_job_id = ?"
        params: list[object] = [int(job_id)]
        if chapter_number is not None:
            sql += " AND chapter_number = ?"
            params.append(int(chapter_number))
        sql += " ORDER BY chapter_number ASC, segment_order ASC, id ASC"
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def get_segments_for_chapter(
        self,
        job_id: int,
        chapter_number: int,
    ) -> list[dict]:
        return self.list_segments(int(job_id), int(chapter_number))

    def set_segment_approval(self, segment_id: int, approved: bool) -> dict:
        cursor = self.connection.execute(
            "UPDATE translation_segments SET is_approved = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (1 if approved else 0, int(segment_id)),
        )
        if int(cursor.rowcount or 0) < 1:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        return self.get_segment(int(segment_id)) or {}

    def get_approved_segments_for_chapter(
        self,
        job_id: int,
        chapter_number: int,
    ) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM translation_segments "
            "WHERE translation_job_id = ? AND chapter_number = ? "
            "AND is_approved = 1 "
            "ORDER BY segment_order ASC, id ASC",
            (int(job_id), int(chapter_number)),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_polish_suggestions(
        self,
        job_id: int,
        chapter_number: int,
        suggestions: list[dict],
    ) -> None:
        rows = self.get_segments_for_chapter(int(job_id), int(chapter_number))
        by_order = {int(row["segment_order"]): row for row in rows}
        suggestion_orders = {
            int(item.get("index") or item.get("segment_order") or 0)
            for item in suggestions
        }
        if suggestion_orders != set(by_order) or len(suggestions) != len(rows):
            raise ValueError("윤문 제안의 문단 개수 또는 순서가 올바르지 않습니다.")
        for suggestion in suggestions:
            order = int(suggestion.get("index") or suggestion.get("segment_order") or 0)
            row = by_order.get(order)
            if row is None:
                raise ValueError("윤문 제안의 문단 순서가 올바르지 않습니다.")
            self.connection.execute(
                "UPDATE translation_segments "
                "SET polish_proposal_text = ?, polish_choice = NULL, "
                "polish_text = NULL, updated_at = datetime('now') "
                "WHERE translation_job_id = ? AND id = ?",
                (
                    str(suggestion.get("polished_text") or ""),
                    int(job_id),
                    int(row["id"]),
                ),
            )

    def apply_polish_selection(
        self,
        segment_id: int,
        use_polished: bool,
        edited_text: str | None,
    ) -> dict:
        row = self.get_segment(int(segment_id))
        if row is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        if use_polished:
            final = str(edited_text or row.get("polish_proposal_text") or "").strip()
            choice = "apply"
            if not final:
                raise ValueError("적용할 윤문 제안이 없습니다.")
        else:
            final = str(row.get("translated_text") or "").strip()
            choice = "keep"
            if not final:
                raise ValueError("유지할 1차 번역문이 없습니다.")
        self.connection.execute(
            "UPDATE translation_segments SET polish_text = ?, polish_choice = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (final, choice, int(segment_id)),
        )
        return self.get_segment(int(segment_id)) or {}

    def apply_all_chapter_polish(
        self,
        job_id: int,
        chapter_number: int,
    ) -> int:
        cursor = self.connection.execute(
            "UPDATE translation_segments "
            "SET polish_text = polish_proposal_text, polish_choice = 'apply', "
            "updated_at = datetime('now') "
            "WHERE translation_job_id = ? AND chapter_number = ?",
            (int(job_id), int(chapter_number)),
        )
        return int(cursor.rowcount or 0)

    def previous_translated_context(
        self,
        job_id: int,
        *,
        before_chapter_number: int,
        before_segment_order: int,
        limit: int = translation_context.PREVIOUS_TRANSLATED_SEGMENT_LIMIT,
    ) -> str:
        rows = self.connection.execute(
            """
            SELECT polish_text, translated_text
            FROM translation_segments
            WHERE translation_job_id = ?
              AND ((translated_text IS NOT NULL AND trim(translated_text) != '')
                OR (polish_text IS NOT NULL AND trim(polish_text) != ''))
              AND (chapter_number < ?
                OR (chapter_number = ? AND segment_order < ?))
            ORDER BY chapter_number DESC, segment_order DESC
            LIMIT ?
            """,
            (
                int(job_id),
                int(before_chapter_number),
                int(before_chapter_number),
                int(before_segment_order),
                max(1, int(limit)),
            ),
        ).fetchall()
        values = []
        for row in reversed(rows):
            values.append(row["polish_text"] or row["translated_text"])
        return translation_context.format_previous_translated_context(values)

    def translation_progress(self, job_id: int) -> dict:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(
                       CASE WHEN trim(COALESCE(translated_text, '')) != ''
                            THEN 1 ELSE 0 END
                   ), 0) AS translated
            FROM translation_segments
            WHERE translation_job_id = ?
            """,
            (int(job_id),),
        ).fetchone()
        total = int(row["total"] or 0) if row else 0
        translated = int(row["translated"] or 0) if row else 0
        return {
            "total_segments": total,
            "translated_segments": translated,
            "pending_segments": max(0, total - translated),
        }

    def chapter_segment_counts(self, job_id: int) -> dict[int, int]:
        rows = self.connection.execute(
            "SELECT chapter_number, COUNT(*) AS segment_count "
            "FROM translation_segments WHERE translation_job_id = ? "
            "GROUP BY chapter_number",
            (int(job_id),),
        ).fetchall()
        return {
            int(row["chapter_number"]): int(row["segment_count"] or 0)
            for row in rows
        }

    def existing_chapter_numbers(self, job_id: int) -> set[int]:
        rows = self.connection.execute(
            "SELECT DISTINCT chapter_number FROM translation_segments "
            "WHERE translation_job_id = ?",
            (int(job_id),),
        ).fetchall()
        return {int(row["chapter_number"]) for row in rows}

    def segment_deletion_stats(
        self,
        job_id: int,
        chapter_numbers: Iterable[int],
    ) -> tuple[int, int]:
        chapters = sorted({int(number) for number in chapter_numbers})
        if not chapters:
            return 0, 0
        placeholders = ",".join("?" * len(chapters))
        rows = self.connection.execute(
            "SELECT translated_text FROM translation_segments "
            "WHERE translation_job_id = ? AND chapter_number IN "
            f"({placeholders})",
            [int(job_id), *chapters],
        ).fetchall()
        translated = sum(
            1 for row in rows if str(row["translated_text"] or "").strip()
        )
        return len(rows), translated

    def commit(self) -> None:
        self.connection.commit()
