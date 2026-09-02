"""SQLite persistence for translation preparation artifacts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

import character_import_analysis


class TranslationPreparationRepository:
    """Store formatting rules, scene contexts, and proper-noun decisions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        timestamp_provider: Callable[[], str],
    ) -> None:
        self.connection = connection
        self.timestamp_provider = timestamp_provider

    def save_formatting_rules(self, job_id: int, rules_json: object) -> None:
        payload = (
            rules_json
            if isinstance(rules_json, str)
            else json.dumps(rules_json, ensure_ascii=False)
        )
        self.connection.execute(
            "UPDATE translation_jobs "
            "SET narrative_formatting_rules_json = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (payload, int(job_id)),
        )

    def get_formatting_rules(self, job_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT narrative_formatting_rules_json FROM translation_jobs "
            "WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        if row is None:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        raw = row["narrative_formatting_rules_json"]
        if raw is None or not str(raw).strip():
            return None
        try:
            loaded = json.loads(str(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            return {"raw": str(raw)}
        return loaded if isinstance(loaded, dict) else {"raw": loaded}

    def save_scene_contexts(self, job_id: int, scenes: list[dict]) -> None:
        stamp = self.timestamp_provider()
        for scene in scenes:
            chapter_number = int(scene["chapter_number"])
            cursor = self.connection.execute(
                """
                INSERT INTO translation_scene_contexts(
                    translation_job_id, chapter_number, scene_order,
                    relationship_tag, mood_tag, situation_note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(job_id),
                    chapter_number,
                    int(scene.get("scene_order") or 0),
                    str(scene.get("relationship_tag") or ""),
                    str(scene.get("mood_tag") or ""),
                    str(scene.get("situation_note") or ""),
                    stamp,
                ),
            )
            self._attach_scene_context_to_segments(
                int(job_id),
                chapter_number,
                int(cursor.lastrowid),
                int(scene.get("start_paragraph_index") or 0),
                int(scene.get("end_paragraph_index") or 0),
            )

    def get_scene_contexts(self, job_id: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM translation_scene_contexts WHERE translation_job_id = ? "
            "ORDER BY chapter_number ASC, scene_order ASC, id ASC",
            (int(job_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_scene_context(self, scene_context_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_scene_contexts WHERE id = ?",
            (int(scene_context_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def delete_scene_contexts_outside_range(
        self,
        job_id: int,
        start_chapter: int,
        end_chapter: int,
    ) -> int:
        cursor = self.connection.execute(
            "DELETE FROM translation_scene_contexts WHERE translation_job_id = ? "
            "AND (chapter_number < ? OR chapter_number > ?)",
            (int(job_id), int(start_chapter), int(end_chapter)),
        )
        return int(cursor.rowcount or 0)

    def scene_context_chapter_numbers(self, job_id: int) -> set[int]:
        rows = self.connection.execute(
            "SELECT DISTINCT chapter_number FROM translation_scene_contexts "
            "WHERE translation_job_id = ?",
            (int(job_id),),
        ).fetchall()
        return {int(row["chapter_number"]) for row in rows}

    def save_proper_nouns(self, job_id: int, nouns: list[dict]) -> None:
        existing = {
            character_import_analysis.strip_tori_text(row["source_term"]).casefold()
            for row in self.connection.execute(
                "SELECT source_term FROM translation_proper_nouns "
                "WHERE translation_job_id = ?",
                (int(job_id),),
            ).fetchall()
        }
        columns = {
            str(row[1])
            for row in self.connection.execute(
                "PRAGMA table_info(translation_proper_nouns)"
            ).fetchall()
        }
        stamp = self.timestamp_provider()
        for noun in nouns:
            source_term = character_import_analysis.strip_tori_text(
                noun.get("source_term")
            )
            key = source_term.casefold()
            if not key or key in existing:
                continue
            source = str(
                noun.get("source") or noun.get("origin") or "ai_detected"
            ).strip()
            if source not in {"character_index", "ai_detected", "user_added"}:
                source = "ai_detected"
            alternatives = noun.get("suggested_alternatives_json")
            if alternatives is None:
                alternatives = {
                    "romanized": str(noun.get("romanized") or "").strip(),
                    "alternatives": [
                        str(item).strip()
                        for item in (noun.get("suggested_alternatives") or [])
                        if str(item).strip()
                    ],
                }
            alternatives_raw = (
                alternatives
                if isinstance(alternatives, str)
                else json.dumps(alternatives, ensure_ascii=False)
            )
            names = [
                "translation_job_id",
                "source_term",
                "term_type",
                "fit_judgment",
                "judgment_reason",
                "suggested_alternatives_json",
                "user_decision",
                "final_term",
            ]
            values: list[object] = [
                int(job_id),
                source_term,
                noun.get("term_type"),
                noun.get("fit_judgment"),
                str(noun.get("judgment_reason") or ""),
                alternatives_raw,
                noun.get("user_decision"),
                noun.get("final_term"),
            ]
            if "source" in columns:
                names.append("source")
                values.append(source)
            if "origin" in columns:
                names.append("origin")
                values.append(source)
            names.append("created_at")
            values.append(stamp)
            placeholders = ", ".join("?" for _ in names)
            self.connection.execute(
                f"INSERT INTO translation_proper_nouns"
                f"({', '.join(names)}) VALUES ({placeholders})",
                values,
            )
            existing.add(key)

    def get_proper_nouns(self, job_id: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM translation_proper_nouns WHERE translation_job_id = ? "
            "ORDER BY id ASC",
            (int(job_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_proper_noun(self, noun_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_proper_nouns WHERE id = ?",
            (int(noun_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def update_proper_noun(
        self,
        job_id: int,
        noun_id: int,
        final_term: str,
        source: str,
        *,
        user_decision: str | None = None,
        term_type: str | None = None,
        source_term: str | None = None,
    ) -> dict:
        columns = {
            str(row[1])
            for row in self.connection.execute(
                "PRAGMA table_info(translation_proper_nouns)"
            ).fetchall()
        }
        assignments = ["final_term = ?"]
        values: list[object] = [str(final_term)]
        if user_decision is not None:
            assignments.append("user_decision = ?")
            values.append(str(user_decision))
        if term_type is not None:
            assignments.append("term_type = ?")
            values.append(str(term_type))
        if source_term is not None:
            assignments.append("source_term = ?")
            values.append(
                character_import_analysis.strip_tori_text(source_term)
            )
        if "source" in columns:
            assignments.append("source = ?")
            values.append(str(source))
        if "origin" in columns:
            assignments.append("origin = ?")
            values.append(str(source))
        values.extend((int(job_id), int(noun_id)))
        cursor = self.connection.execute(
            f"UPDATE translation_proper_nouns SET {', '.join(assignments)} "
            "WHERE translation_job_id = ? AND id = ?",
            values,
        )
        if int(cursor.rowcount or 0) < 1:
            raise LookupError("고유명사 항목을 찾을 수 없습니다.")
        return self.get_proper_noun(int(noun_id)) or {}

    def apply_proper_noun_ai_fields(
        self,
        job_id: int,
        noun_id: int,
        *,
        fit_judgment: str | None,
        judgment_reason: str,
        romanized: str,
        suggested_alternatives: list[str],
        term_type: str | None = None,
    ) -> dict:
        alternatives = {
            "romanized": str(romanized or "").strip(),
            "alternatives": [
                str(item).strip()
                for item in (suggested_alternatives or [])
                if str(item).strip()
            ],
        }
        assignments = [
            "fit_judgment = ?",
            "judgment_reason = ?",
            "suggested_alternatives_json = ?",
            "user_decision = NULL",
            "final_term = NULL",
        ]
        values: list[object] = [
            fit_judgment,
            str(judgment_reason or ""),
            json.dumps(alternatives, ensure_ascii=False),
        ]
        if term_type is not None:
            assignments.append("term_type = ?")
            values.append(str(term_type))
        values.extend((int(job_id), int(noun_id)))
        cursor = self.connection.execute(
            f"UPDATE translation_proper_nouns SET {', '.join(assignments)} "
            "WHERE translation_job_id = ? AND id = ?",
            values,
        )
        if int(cursor.rowcount or 0) < 1:
            raise LookupError("고유명사 항목을 찾을 수 없습니다.")
        return self.get_proper_noun(int(noun_id)) or {}

    def delete_proper_noun(self, job_id: int, noun_id: int) -> None:
        cursor = self.connection.execute(
            "DELETE FROM translation_proper_nouns "
            "WHERE translation_job_id = ? AND id = ?",
            (int(job_id), int(noun_id)),
        )
        if int(cursor.rowcount or 0) < 1:
            raise LookupError("고유명사 항목을 찾을 수 없습니다.")

    def suppress_proper_noun_term(self, job_id: int, source_term: str) -> None:
        key = character_import_analysis.strip_tori_text(source_term).casefold()
        if not key:
            return
        self.connection.execute(
            "INSERT OR IGNORE INTO translation_proper_noun_suppressions"
            "(translation_job_id, source_term_key, created_at) VALUES (?, ?, ?)",
            (int(job_id), key, self.timestamp_provider()),
        )

    def unsuppress_proper_noun_term(self, job_id: int, source_term: str) -> None:
        key = character_import_analysis.strip_tori_text(source_term).casefold()
        if not key:
            return
        self.connection.execute(
            "DELETE FROM translation_proper_noun_suppressions "
            "WHERE translation_job_id = ? AND source_term_key = ?",
            (int(job_id), key),
        )

    def suppressed_proper_noun_keys(self, job_id: int) -> set[str]:
        rows = self.connection.execute(
            "SELECT source_term_key FROM translation_proper_noun_suppressions "
            "WHERE translation_job_id = ?",
            (int(job_id),),
        ).fetchall()
        keys: set[str] = set()
        for row in rows:
            key = str(row["source_term_key"] or "").strip()
            if key:
                keys.add(key)
        return keys

    def confirm_all_proper_nouns(self, job_id: int) -> None:
        cursor = self.connection.execute(
            "UPDATE translation_jobs SET proper_nouns_confirmed = 1, "
            "updated_at = datetime('now') WHERE id = ?",
            (int(job_id),),
        )
        if int(cursor.rowcount or 0) < 1:
            raise LookupError("번역 작업을 찾을 수 없습니다.")

    def clear_proper_nouns_confirmed(self, job_id: int) -> None:
        cursor = self.connection.execute(
            "UPDATE translation_jobs SET proper_nouns_confirmed = 0, "
            "updated_at = datetime('now') WHERE id = ?",
            (int(job_id),),
        )
        if int(cursor.rowcount or 0) < 1:
            raise LookupError("번역 작업을 찾을 수 없습니다.")

    def source_text(self, job_id: int, chapter_number: int | None = None) -> str:
        sql = (
            "SELECT source_text FROM translation_segments "
            "WHERE translation_job_id = ?"
        )
        params: list[object] = [int(job_id)]
        if chapter_number is not None:
            sql += " AND chapter_number = ?"
            params.append(int(chapter_number))
        sql += " ORDER BY chapter_number ASC, segment_order ASC, id ASC"
        rows = self.connection.execute(sql, params).fetchall()
        return "\n\n".join(
            str(row["source_text"] or "").strip()
            for row in rows
            if str(row["source_text"] or "").strip()
        )

    def chapter_numbers(self, job_id: int) -> list[int]:
        rows = self.connection.execute(
            "SELECT DISTINCT chapter_number FROM translation_segments "
            "WHERE translation_job_id = ? ORDER BY chapter_number ASC",
            (int(job_id),),
        ).fetchall()
        return [int(row["chapter_number"]) for row in rows]

    def chapter_segment_count(self, job_id: int, chapter_number: int) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM translation_segments "
            "WHERE translation_job_id = ? AND chapter_number = ?",
            (int(job_id), int(chapter_number)),
        ).fetchone()
        return int(row["n"] or 0) if row else 0

    def chapter_has_scene_contexts(
        self,
        job_id: int,
        chapter_number: int,
    ) -> bool:
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM translation_scene_contexts "
            "WHERE translation_job_id = ? AND chapter_number = ?",
            (int(job_id), int(chapter_number)),
        ).fetchone()
        return bool(row and int(row["n"] or 0) > 0)

    def mark_proper_nouns_extracted(self, job_id: int) -> None:
        self.connection.execute(
            "UPDATE translation_jobs SET proper_nouns_extracted = 1, "
            "updated_at = datetime('now') WHERE id = ?",
            (int(job_id),),
        )

    def set_preparation_status(self, job_id: int, status: str) -> None:
        self.connection.execute(
            "UPDATE translation_jobs SET status = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (str(status), int(job_id)),
        )

    def record_pipeline_failure(
        self,
        job_id: int,
        step: str,
        message: str,
    ) -> None:
        self.connection.execute(
            "UPDATE translation_jobs SET pipeline_failed_step = ?, "
            "pipeline_error = ?, updated_at = datetime('now') WHERE id = ?",
            (str(step), str(message)[:2000], int(job_id)),
        )

    def clear_pipeline_failure(self, job_id: int) -> None:
        self.connection.execute(
            "UPDATE translation_jobs SET pipeline_failed_step = NULL, "
            "pipeline_error = NULL, updated_at = datetime('now') WHERE id = ?",
            (int(job_id),),
        )

    def commit(self) -> None:
        self.connection.commit()

    def _attach_scene_context_to_segments(
        self,
        job_id: int,
        chapter_number: int,
        scene_id: int,
        start_index: int,
        end_index: int,
    ) -> None:
        rows = self.connection.execute(
            "SELECT id FROM translation_segments "
            "WHERE translation_job_id = ? AND chapter_number = ? "
            "ORDER BY segment_order ASC, id ASC",
            (int(job_id), int(chapter_number)),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        lo = max(0, int(start_index))
        hi = min(len(ids) - 1, int(end_index))
        if not ids or hi < lo:
            return
        for segment_id in ids[lo : hi + 1]:
            self.connection.execute(
                "UPDATE translation_segments SET scene_context_id = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (int(scene_id), int(segment_id)),
            )
