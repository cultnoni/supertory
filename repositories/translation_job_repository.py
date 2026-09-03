"""SQLite persistence for translation jobs and their seeded segments."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable


class TranslationJobRepository:
    """Keep translation job SQL behind a replaceable repository contract."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        scene_loader: Callable[[sqlite3.Connection, int], list[dict]],
        paragraph_splitter: Callable[[str], list[str]],
        separator_checker: Callable[[str], bool],
        timestamp_provider: Callable[[], str],
    ) -> None:
        self.connection = connection
        self.scene_loader = scene_loader
        self.paragraph_splitter = paragraph_splitter
        self.separator_checker = separator_checker
        self.timestamp_provider = timestamp_provider

    def create_job(
        self,
        project_id: int,
        target_language: str,
        culture_localization_level: str,
        start_chapter: int,
        end_chapter: int,
        translate_all_chapters: bool,
        *,
        style_guide_json: str | None = None,
    ) -> dict:
        stamp = self.timestamp_provider()
        cursor = self.connection.execute(
            """
            INSERT INTO translation_jobs(
                local_project_id, target_language, cliffhanger_chapter,
                style_guide_json, culture_localization_level, status,
                created_at, updated_at, start_chapter, end_chapter,
                translate_all_chapters
            ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
            """,
            (
                int(project_id),
                str(target_language),
                int(end_chapter),
                style_guide_json,
                str(culture_localization_level),
                stamp,
                stamp,
                int(start_chapter),
                int(end_chapter),
                1 if translate_all_chapters else 0,
            ),
        )
        return self.get_job(int(cursor.lastrowid)) or {}

    def project_exists(self, project_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM project WHERE id = ? AND deleted_at IS NULL",
            (int(project_id),),
        ).fetchone()
        return row is not None

    def get_job(self, job_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_job_for_project(self, project_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM translation_jobs WHERE local_project_id = ? "
            "ORDER BY datetime(updated_at) DESC, id DESC LIMIT 1",
            (int(project_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_jobs_for_project(self, project_id: int) -> list[dict]:
        return self.list_jobs(int(project_id))

    def list_jobs(self, project_id: int | None = None) -> list[dict]:
        sql = """
            SELECT j.*,
                   p.title AS project_title,
                   CASE
                     WHEN TRIM(COALESCE(sp.logline_translated, '')) != ''
                       OR TRIM(COALESCE(sp.synopsis_translated, '')) != ''
                     THEN 1 ELSE 0
                   END AS has_submission_package,
                   sp.generated_at AS submission_generated_at
            FROM translation_jobs j
            JOIN project p ON p.id = j.local_project_id
            LEFT JOIN translation_submission_package sp
              ON sp.translation_job_id = j.id
            WHERE p.deleted_at IS NULL
        """
        params: list[object] = []
        if project_id is not None:
            sql += " AND j.local_project_id = ?"
            params.append(int(project_id))
        sql += " ORDER BY datetime(j.updated_at) DESC, j.id DESC"
        rows = self.connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def update_job_settings(self, job_id: int, values: dict) -> dict:
        allowed = {
            "start_chapter",
            "end_chapter",
            "translate_all_chapters",
            "cliffhanger_chapter",
            "culture_localization_level",
        }
        patch = {key: values[key] for key in allowed if key in values}
        if patch:
            assignments = ", ".join(f"{key} = ?" for key in patch)
            self.connection.execute(
                f"UPDATE translation_jobs SET {assignments}, "
                "updated_at = datetime('now') WHERE id = ?",
                [*patch.values(), int(job_id)],
            )
        job = self.get_job(job_id)
        if job is None:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        return job

    def update_job_status(self, job_id: int, status: str) -> dict:
        cursor = self.connection.execute(
            "UPDATE translation_jobs SET status = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (str(status), int(job_id)),
        )
        if int(cursor.rowcount or 0) < 1:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        return self.get_job(job_id) or {}

    def delete_segments_outside_range(
        self,
        job_id: int,
        start_chapter: int,
        end_chapter: int,
    ) -> int:
        cursor = self.connection.execute(
            "DELETE FROM translation_segments WHERE translation_job_id = ? "
            "AND (chapter_number < ? OR chapter_number > ?)",
            (int(job_id), int(start_chapter), int(end_chapter)),
        )
        return int(cursor.rowcount or 0)

    def seed_segments_for_chapters(
        self,
        job_id: int,
        chapter_numbers: list[int],
    ) -> int:
        job = self.get_job(job_id)
        if job is None:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        requested = {int(number) for number in chapter_numbers if int(number) > 0}
        if not requested:
            return 0
        existing = {
            int(row["chapter_number"])
            for row in self.connection.execute(
                "SELECT DISTINCT chapter_number FROM translation_segments "
                "WHERE translation_job_id = ?",
                (int(job_id),),
            )
        }
        scenes = self.scene_loader(self.connection, int(job["local_project_id"]))
        inserted = 0
        stamp = self.timestamp_provider()
        for episode_index, scene in enumerate(scenes, start=1):
            if episode_index not in requested or episode_index in existing:
                continue
            paragraphs = self.paragraph_splitter(str(scene.get("content_md") or ""))
            for order, paragraph in enumerate(paragraphs, start=1):
                passthrough = self.separator_checker(paragraph)
                self.connection.execute(
                    """
                    INSERT INTO translation_segments(
                        translation_job_id, chapter_number, segment_order,
                        source_text, translated_text, polish_text,
                        needs_manual_review, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        int(job_id),
                        episode_index,
                        order,
                        paragraph,
                        paragraph if passthrough else None,
                        paragraph if passthrough else None,
                        stamp,
                        stamp,
                    ),
                )
                inserted += 1
        print(
            f"[translation-seed] job={int(job_id)} "
            f"start_chapter={min(requested)} end_chapter={max(requested)} "
            f"seeded={inserted}",
            flush=True,
        )
        return inserted

    def manuscript_episode_catalog(self, project_id: int) -> list[dict]:
        catalog: list[dict] = []
        for episode_index, scene in enumerate(
            self.scene_loader(self.connection, int(project_id)),
            start=1,
        ):
            paragraphs = self.paragraph_splitter(str(scene.get("content_md") or ""))
            scene_title = str(scene.get("title") or "").strip()
            chapter_title = str(scene.get("chapter_title") or "").strip()
            folder_path = str(scene.get("folder_path") or "").strip()
            catalog.append({
                "number": episode_index,
                "title": scene_title or chapter_title or f"{episode_index}화",
                "chapter_title": chapter_title,
                "folder_path": folder_path,
                "segment_count": len(paragraphs),
            })
        return catalog
