"""SQLite persistence for scene metadata and append-only revisions.

Existing ``scene`` / ``scene_revision`` tables and triggers stay as-is.
This module only relocates the SQL that used to live in HTTP handlers.
"""

from __future__ import annotations

import sqlite3

ROW_VERSION_CONFLICT_MESSAGE = (
    "다른 화면에서 이 씬이 변경되었습니다. 새로 열고 다시 저장해 주세요."
)


def _as_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


class SceneContentRepository:
    """Read and write scene rows and current revisions on a SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_scene_meta(self, scene_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT id, project_id, chapter_id, title, synopsis_md, notes_md, status, "
            "goal_word_count, goal_metric, reference_links_json, sort_order, "
            "created_at, updated_at, row_version "
            "FROM scene WHERE id = ? AND deleted_at IS NULL",
            (int(scene_id),),
        ).fetchone()
        return _as_dict(row)

    def get_current_revision(self, scene_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT r.id, r.scene_id, r.revision_no, r.content_md, r.word_count, "
            "r.save_note, r.is_checkpoint, r.is_current, r.created_at, s.row_version "
            "FROM scene_revision AS r "
            "JOIN scene AS s ON s.id = r.scene_id "
            "WHERE r.scene_id = ? AND r.is_current = 1",
            (int(scene_id),),
        ).fetchone()
        return _as_dict(row)

    def save_new_revision(
        self,
        scene_id: int,
        content_html: str,
        expected_row_version: int,
        *,
        save_note: str = "저장",
        word_count: int = 0,
    ) -> dict:
        """Insert the next current revision when content actually changed.

        ``expected_row_version`` of 0 skips the optimistic lock, matching the
        previous ``save_scene`` / ``_write_scene_content`` split.
        """
        scene_id = int(scene_id)
        if expected_row_version:
            scene = self.get_scene_meta(scene_id)
            if scene is None:
                raise ValueError("씬을 찾을 수 없습니다.")
            if scene["row_version"] != expected_row_version:
                raise ValueError(ROW_VERSION_CONFLICT_MESSAGE)

        current = self.connection.execute(
            "SELECT id, revision_no, content_md, word_count FROM scene_revision "
            "WHERE scene_id = ? AND is_current = 1",
            (scene_id,),
        ).fetchone()
        if current is None:
            self.connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, save_note) "
                "VALUES (?, 1, ?, ?, ?)",
                (scene_id, content_html, int(word_count), save_note),
            )
            saved = self.get_current_revision(scene_id)
            if saved is None:
                raise ValueError("현재 원고를 찾을 수 없습니다.")
            return saved
        if current["content_md"] == content_html:
            saved = self.get_current_revision(scene_id)
            if saved is None:
                return {
                    "id": current["id"],
                    "scene_id": scene_id,
                    "revision_no": current["revision_no"],
                    "content_md": current["content_md"],
                    "word_count": current["word_count"],
                    "row_version": 0,
                }
            return saved
        cursor = self.connection.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, save_note, is_current) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (
                scene_id,
                current["revision_no"] + 1,
                content_html,
                int(word_count),
                save_note,
            ),
        )
        self.connection.execute(
            "UPDATE scene_revision SET is_current = CASE "
            "WHEN id = ? THEN 1 WHEN id = ? THEN 0 ELSE is_current END "
            "WHERE id IN (?, ?)",
            (cursor.lastrowid, current["id"], cursor.lastrowid, current["id"]),
        )
        saved = self.get_current_revision(scene_id)
        if saved is None:
            raise ValueError("현재 원고를 찾을 수 없습니다.")
        return saved

    def update_scene_meta(self, scene_id: int, values: dict) -> dict:
        """Update manuscript metadata and bump ``row_version`` (same SQL as before)."""
        scene_id = int(scene_id)
        title = values["title"]
        synopsis_md = values.get("synopsis_md", "")
        notes_md = values.get("notes_md", "")
        status = values["status"]
        goal_word_count = values.get("goal_word_count", 0)
        goal_metric = values.get("goal_metric", "chars_with_space")
        if "reference_links_json" in values:
            self.connection.execute(
                "UPDATE scene SET title = ?, synopsis_md = ?, notes_md = ?, status = ?, "
                "goal_word_count = ?, goal_metric = ?, reference_links_json = ?, "
                "row_version = row_version + 1 WHERE id = ?",
                (
                    title,
                    synopsis_md,
                    notes_md,
                    status,
                    goal_word_count,
                    goal_metric,
                    values["reference_links_json"],
                    scene_id,
                ),
            )
        else:
            self.connection.execute(
                "UPDATE scene SET title = ?, synopsis_md = ?, notes_md = ?, status = ?, "
                "goal_word_count = ?, goal_metric = ?, row_version = row_version + 1 WHERE id = ?",
                (
                    title,
                    synopsis_md,
                    notes_md,
                    status,
                    goal_word_count,
                    goal_metric,
                    scene_id,
                ),
            )
        updated = self.get_scene_meta(scene_id)
        if updated is None:
            raise ValueError("씬을 찾을 수 없습니다.")
        return updated

    def bump_row_version(self, scene_id: int) -> dict:
        """Bump version and ``updated_at`` (phone-draft merge path)."""
        scene_id = int(scene_id)
        self.connection.execute(
            "UPDATE scene SET row_version = row_version + 1, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ?",
            (scene_id,),
        )
        updated = self.get_scene_meta(scene_id)
        if updated is None:
            raise ValueError("씬을 찾을 수 없습니다.")
        return updated
