"""Persistence for translation dictionary, QA, and submission extras."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable


class TranslationExtrasRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        timestamp_provider: Callable[[], str],
    ) -> None:
        self.connection = connection
        self.timestamp_provider = timestamp_provider

    def get_cached_word_lookup(
        self,
        segment_id: int,
        word: str,
    ) -> dict | None:
        row = self.connection.execute(
            "SELECT result_json FROM translation_word_lookup_cache "
            "WHERE segment_id = ? AND word = ?",
            (int(segment_id), str(word)),
        ).fetchone()
        if row is None:
            return None
        try:
            result = json.loads(str(row["result_json"] or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return result if isinstance(result, dict) else None

    def save_word_lookup_cache(
        self,
        segment_id: int,
        word: str,
        result: dict,
    ) -> None:
        if self.get_segment(int(segment_id)) is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        self.connection.execute(
            """
            INSERT INTO translation_word_lookup_cache(
                segment_id, word, result_json, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(segment_id, word) DO UPDATE SET
                result_json = excluded.result_json,
                updated_at = excluded.updated_at
            """,
            (
                int(segment_id),
                str(word),
                json.dumps(result, ensure_ascii=False),
                self.timestamp_provider(),
            ),
        )

    def get_cached_word_context(
        self,
        segment_id: int,
        word: str,
    ) -> dict | None:
        row = self.connection.execute(
            "SELECT explanation FROM translation_word_context_cache "
            "WHERE segment_id = ? AND word = ?",
            (int(segment_id), str(word)),
        ).fetchone()
        if row is None:
            return None
        return {
            "segment_id": int(segment_id),
            "word": str(word),
            "explanation": str(row["explanation"] or ""),
            "source": "cache",
        }

    def save_word_context_cache(
        self,
        segment_id: int,
        word: str,
        explanation: str,
    ) -> None:
        if self.get_segment(int(segment_id)) is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        self.connection.execute(
            """
            INSERT INTO translation_word_context_cache(
                segment_id, word, explanation
            ) VALUES (?, ?, ?)
            ON CONFLICT(segment_id, word) DO UPDATE SET
                explanation = excluded.explanation
            """,
            (int(segment_id), str(word), str(explanation)),
        )

    def save_qa_message(
        self,
        job_id: int,
        question: str,
        answer: str,
        *,
        segment_id: int | None = None,
        dragged_text: str | None = None,
    ) -> dict:
        if self.get_job(int(job_id)) is None:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        if segment_id is not None and self.get_segment_for_job(
            int(segment_id), int(job_id)
        ) is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        stamp = self.timestamp_provider()
        user = self.connection.execute(
            """
            INSERT INTO translation_chat_messages(
                translation_job_id, segment_id, dragged_text,
                role, message, created_at
            ) VALUES (?, ?, ?, 'user', ?, ?)
            """,
            (
                int(job_id),
                int(segment_id) if segment_id is not None else None,
                str(dragged_text).strip() if dragged_text else None,
                str(question),
                stamp,
            ),
        )
        tori = self.connection.execute(
            """
            INSERT INTO translation_chat_messages(
                translation_job_id, segment_id, dragged_text,
                role, message, created_at
            ) VALUES (?, ?, NULL, 'tori', ?, ?)
            """,
            (
                int(job_id),
                int(segment_id) if segment_id is not None else None,
                str(answer),
                self.timestamp_provider(),
            ),
        )
        self.connection.execute(
            "UPDATE translation_jobs SET updated_at = datetime('now') "
            "WHERE id = ?",
            (int(job_id),),
        )
        return {
            "user": self.get_qa_message(int(user.lastrowid)) or {},
            "tori": self.get_qa_message(int(tori.lastrowid)) or {},
        }

    def get_qa_message(self, message_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_chat_messages WHERE id = ?",
            (int(message_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_qa_history(
        self,
        job_id: int,
        *,
        segment_id: int | None = None,
        limit: int | None = None,
        scoped: bool = False,
    ) -> list[dict]:
        sql = (
            "SELECT * FROM translation_chat_messages "
            "WHERE translation_job_id = ?"
        )
        params: list[object] = [int(job_id)]
        if scoped:
            if segment_id is None:
                sql += " AND segment_id IS NULL"
            else:
                sql += " AND segment_id = ?"
                params.append(int(segment_id))
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        rows = self.connection.execute(sql, params).fetchall()
        return [dict(row) for row in reversed(rows)]

    def get_all_qa_history(self, job_id: int) -> list[dict]:
        return self.get_qa_history(int(job_id))

    def save_submission_package(
        self,
        job_id: int,
        logline: str,
        synopsis: str,
    ) -> dict:
        if self.get_job(int(job_id)) is None:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        sample_range = self._sample_chapter_range(int(job_id))
        existing = self.get_submission_package(int(job_id))
        stamp = self.timestamp_provider()
        if existing is None:
            cursor = self.connection.execute(
                """
                INSERT INTO translation_submission_package(
                    translation_job_id, synopsis_translated,
                    logline_translated, sample_chapters_range, generated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(job_id),
                    str(synopsis),
                    str(logline),
                    sample_range,
                    stamp,
                ),
            )
            package_id = int(cursor.lastrowid)
        else:
            package_id = int(existing["id"])
            self.connection.execute(
                """
                UPDATE translation_submission_package
                SET synopsis_translated = ?, logline_translated = ?,
                    sample_chapters_range = ?, generated_at = ?
                WHERE id = ?
                """,
                (
                    str(synopsis),
                    str(logline),
                    sample_range,
                    stamp,
                    package_id,
                ),
            )
        return self._get_submission_package_by_id(package_id) or {}

    def get_submission_package(self, job_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_submission_package "
            "WHERE translation_job_id = ? ORDER BY id DESC LIMIT 1",
            (int(job_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def _get_submission_package_by_id(self, package_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_submission_package WHERE id = ?",
            (int(package_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_job(self, job_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_segment(self, segment_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_segments WHERE id = ?",
            (int(segment_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_segment_for_job(
        self,
        segment_id: int,
        job_id: int,
    ) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_segments "
            "WHERE id = ? AND translation_job_id = ?",
            (int(segment_id), int(job_id)),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_project_synopsis(self, project_id: int) -> str:
        row = self.connection.execute(
            "SELECT description_md FROM project WHERE id = ?",
            (int(project_id),),
        ).fetchone()
        return str(row["description_md"] or "").strip() if row else ""

    def get_project_title(self, project_id: int) -> str:
        if not project_id:
            return ""
        row = self.connection.execute(
            "SELECT title FROM project WHERE id = ?",
            (int(project_id),),
        ).fetchone()
        return str(row["title"] or "").strip() if row else ""

    def get_completed_segments(self, job_id: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM translation_segments "
            "WHERE translation_job_id = ? "
            "AND trim(CASE "
            "WHEN trim(COALESCE(polish_text, '')) != '' THEN polish_text "
            "ELSE COALESCE(translated_text, '') END) != '' "
            "ORDER BY chapter_number ASC, segment_order ASC, id ASC",
            (int(job_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def _sample_chapter_range(self, job_id: int) -> str:
        job = self.get_job(int(job_id))
        if job is None:
            return ""
        row = self.connection.execute(
            "SELECT MIN(chapter_number) AS first_chapter, "
            "MAX(chapter_number) AS last_chapter "
            "FROM translation_segments WHERE translation_job_id = ?",
            (int(job_id),),
        ).fetchone()
        first = job.get("start_chapter")
        last = job.get("end_chapter") or job.get("cliffhanger_chapter")
        if first in (None, "") and row is not None:
            first = row["first_chapter"]
        if last in (None, "") and row is not None:
            last = row["last_chapter"]
        return (
            f"{int(first)}-{int(last)}"
            if first not in (None, "") and last not in (None, "")
            else ""
        )

    def commit(self) -> None:
        self.connection.commit()
