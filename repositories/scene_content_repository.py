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
                raise ValueError("회차를 찾을 수 없습니다.")
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
            self._note_long_form_summary(scene_id, content_html)
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
        self._note_long_form_summary(scene_id, content_html)
        return saved

    def _note_long_form_summary(self, scene_id: int, content_html: str) -> None:
        try:
            from feedback_pipeline.literature_summary import note_scene_content_for_long_form

            note_scene_content_for_long_form(self.connection, scene_id, content_html)
        except Exception:
            return

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
            raise ValueError("회차를 찾을 수 없습니다.")
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
            raise ValueError("회차를 찾을 수 없습니다.")
        return updated

    # —— 이전 버전(이력 조회)과 체크포인트 표시 ——
    # scene_revision은 수정·삭제가 막혀 있으므로(트리거) 읽기만 하고,
    # 체크포인트는 scene_revision_mark(마이그레이션 107)에 따로 기록한다.

    def list_revisions(
        self, scene_id: int, *, limit: int = 200, offset: int = 0
    ) -> tuple[list[dict], int]:
        """Newest first, without bodies. Returns (rows, total)."""
        scene_id = int(scene_id)
        total = self.connection.execute(
            "SELECT COUNT(*) FROM scene_revision WHERE scene_id = ?", (scene_id,)
        ).fetchone()[0]
        rows = self.connection.execute(
            "SELECT revision_no, word_count, save_note, is_current, created_at "
            "FROM scene_revision WHERE scene_id = ? "
            "ORDER BY revision_no DESC LIMIT ? OFFSET ?",
            (scene_id, max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
        return [dict(row) for row in rows], int(total or 0)

    def get_revision(self, scene_id: int, revision_no: int) -> dict | None:
        row = self.connection.execute(
            "SELECT revision_no, content_md, word_count, save_note, is_current, created_at "
            "FROM scene_revision WHERE scene_id = ? AND revision_no = ?",
            (int(scene_id), int(revision_no)),
        ).fetchone()
        return _as_dict(row)

    def word_count_before(self, scene_id: int, revision_no: int) -> int | None:
        """Word count of the revision just before ``revision_no`` (None if first)."""
        row = self.connection.execute(
            "SELECT word_count FROM scene_revision WHERE scene_id = ? AND revision_no < ? "
            "ORDER BY revision_no DESC LIMIT 1",
            (int(scene_id), int(revision_no)),
        ).fetchone()
        return int(row[0] or 0) if row is not None else None

    def list_marks(self, scene_id: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT revision_no, reason, label, created_at FROM scene_revision_mark "
            "WHERE scene_id = ? ORDER BY revision_no DESC, id ASC",
            (int(scene_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_mark(
        self, scene_id: int, revision_no: int, reason: str, label: str = ""
    ) -> None:
        """Mark a revision. The same reason twice keeps one row (label updated)."""
        self.connection.execute(
            "INSERT INTO scene_revision_mark(scene_id, revision_no, reason, label) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(scene_id, revision_no, reason) DO UPDATE SET label = excluded.label",
            (int(scene_id), int(revision_no), str(reason), str(label or "")),
        )

    def remove_marks(
        self, scene_id: int, revision_no: int, reason: str | None = None
    ) -> int:
        if reason is None:
            cursor = self.connection.execute(
                "DELETE FROM scene_revision_mark WHERE scene_id = ? AND revision_no = ?",
                (int(scene_id), int(revision_no)),
            )
        else:
            cursor = self.connection.execute(
                "DELETE FROM scene_revision_mark "
                "WHERE scene_id = ? AND revision_no = ? AND reason = ?",
                (int(scene_id), int(revision_no), str(reason)),
            )
        return int(cursor.rowcount or 0)

    def mark_current_before_change(
        self,
        scene_id: int,
        reason: str,
        *,
        next_content: str | None = None,
        skip_blank: bool = True,
    ) -> int | None:
        """Mark the current revision before something replaces it.

        When ``next_content`` is given and equals the current body, nothing is
        replaced, so nothing is marked. An empty body (a brand-new scene) is not
        worth a checkpoint unless ``skip_blank`` is False. Returns the marked
        revision number.
        """
        current = self.connection.execute(
            "SELECT revision_no, content_md FROM scene_revision "
            "WHERE scene_id = ? AND is_current = 1",
            (int(scene_id),),
        ).fetchone()
        if current is None:
            return None
        body = str(current["content_md"] or "")
        if skip_blank and not body.strip():
            return None
        if next_content is not None and body == str(next_content):
            return None
        revision_no = int(current["revision_no"])
        self.add_mark(scene_id, revision_no, reason)
        return revision_no
