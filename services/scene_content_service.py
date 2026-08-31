"""Manuscript save rules: optimistic lock, revision-only-on-change, shared writes."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

import sqlite3

from repositories.scene_content_repository import (
    ROW_VERSION_CONFLICT_MESSAGE,
    SceneContentRepository,
)

SCENE_STATUSES = {"idea", "outline", "draft", "revision", "complete"}


def _signed_in_user_id() -> str | None:
    """Return the cached session user id, or None. Never talks to the network."""
    try:
        from sync.auth_session import load_session

        payload = load_session() or {}
    except Exception:  # noqa: BLE001 — mirroring is optional
        return None
    user_id = str(payload.get("user_id") or "").strip()
    return user_id or None


class SceneContentRepositoryContract(Protocol):
    def get_scene_meta(self, scene_id: int) -> dict | None: ...

    def get_current_revision(self, scene_id: int) -> dict | None: ...

    def save_new_revision(
        self,
        scene_id: int,
        content_html: str,
        expected_row_version: int,
        *,
        save_note: str = "저장",
        word_count: int = 0,
    ) -> dict: ...

    def update_scene_meta(self, scene_id: int, values: dict) -> dict: ...

    def bump_row_version(self, scene_id: int) -> dict: ...


class SceneContentService:
    """Editor save, import overwrite, and phone-draft merge share this path."""

    def __init__(
        self,
        *,
        database: Callable[[], AbstractContextManager[sqlite3.Connection]],
        word_count: Callable[[str], int],
        parse_reference_links: Callable[[object], list[dict]],
        goal_metrics: set[str],
        repository_factory: Callable[
            [sqlite3.Connection], SceneContentRepositoryContract
        ] = SceneContentRepository,
        mirror_after_persist: Callable[[int, str, str, int], Any] | None = None,
    ) -> None:
        self.database = database
        self.word_count = word_count
        self.parse_reference_links = parse_reference_links
        self.goal_metrics = goal_metrics
        self.repository_factory = repository_factory
        self.mirror_after_persist = mirror_after_persist

    def persist_scene(
        self,
        scene_id: int,
        content_html: str | None,
        meta: dict,
        expected_row_version,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict:
        """Editor PUT: bump meta; insert a revision only when a body was sent and changed.

        ``content_html is None`` means a metadata-only request (title, status, …).
        An explicit empty string still counts as a body and may create a revision.
        """
        fields = meta if isinstance(meta, dict) else {}
        content_provided = content_html is not None
        expected_version = int(expected_row_version or 0)
        save_note = str(fields.get("save_note", "") or "").strip() or "저장"
        links_json = None
        if "reference_links" in fields:
            links_json = json.dumps(
                self.parse_reference_links(fields.get("reference_links")),
                ensure_ascii=False,
            )

        def run(conn: sqlite3.Connection) -> dict:
            repo = self.repository_factory(conn)
            scene = repo.get_scene_meta(scene_id)
            if scene is None:
                raise ValueError("씬을 찾을 수 없습니다.")
            if expected_version and scene["row_version"] != expected_version:
                raise ValueError(ROW_VERSION_CONFLICT_MESSAGE)
            previous_status = str(scene["status"] or "")
            if "title" in fields:
                title = str(fields.get("title") or "").strip()
                if not title:
                    raise ValueError("씬 제목을 입력해 주세요.")
            else:
                title = str(scene.get("title") or "").strip()
                if not title:
                    raise ValueError("씬 제목을 입력해 주세요.")
            if "status" in fields:
                status = str(fields.get("status") or "idea")
                if status not in SCENE_STATUSES:
                    raise ValueError("올바르지 않은 씬 상태입니다.")
            else:
                status = str(scene.get("status") or "idea")
                if status not in SCENE_STATUSES:
                    status = "idea"
            if "goal_word_count" in fields:
                goal_count = max(0, int(fields.get("goal_word_count", 0) or 0))
            else:
                goal_count = max(0, int(scene.get("goal_word_count") or 0))
            if "goal_metric" in fields:
                goal_metric = str(
                    fields.get("goal_metric") or "chars_with_space"
                )
            else:
                goal_metric = str(
                    scene.get("goal_metric") or "chars_with_space"
                )
            if goal_metric not in self.goal_metrics:
                raise ValueError("목표 글자 수 기준이 올바르지 않습니다.")
            values = {
                "title": title,
                "synopsis_md": (
                    str(fields.get("synopsis_md") or "")
                    if "synopsis_md" in fields
                    else str(scene.get("synopsis_md") or "")
                ),
                "notes_md": (
                    str(fields.get("notes_md") or "")
                    if "notes_md" in fields
                    else str(scene.get("notes_md") or "")
                ),
                "status": status,
                "goal_word_count": goal_count,
                "goal_metric": goal_metric,
            }
            if links_json is not None:
                values["reference_links_json"] = links_json
            updated = repo.update_scene_meta(scene_id, values)
            current = repo.get_current_revision(scene_id)
            if current is None:
                raise ValueError("현재 원고를 찾을 수 없습니다.")
            revision_no = int(current["revision_no"])
            words = int(current["word_count"] or 0)
            body = str(current.get("content_md") or "")
            if content_provided:
                content = str(content_html)
                if current["content_md"] != content:
                    words = self.word_count(content)
                    saved = repo.save_new_revision(
                        scene_id,
                        content,
                        0,
                        save_note=save_note,
                        word_count=words,
                    )
                    revision_no = int(saved["revision_no"])
                    words = int(saved["word_count"] or 0)
                    body = content
            return {
                "ok": True,
                "row_version": int(updated["row_version"]),
                "revision_no": revision_no,
                "word_count": words,
                "status": status,
                "previous_status": previous_status,
                "project_id": int(scene["project_id"]),
                "title": title,
                "mirror_content": body,
            }

        if connection is not None:
            result = run(connection)
        else:
            with self.database() as conn:
                result = run(conn)
        project_id = result.pop("project_id", None)
        mirror_title = str(result.pop("title", "") or "")
        mirror_content = str(result.pop("mirror_content", "") or "")
        self._schedule_browser_mirror(
            scene_id, mirror_content, mirror_title, project_id
        )
        return result

    def _schedule_browser_mirror(
        self,
        scene_id: int,
        content_html: str,
        title: str,
        project_id: object,
    ) -> None:
        """Best-effort remote mirror. Must never fail the local save."""
        try:
            local_project_id = int(project_id)
        except (TypeError, ValueError):
            return
        try:
            hook = self.mirror_after_persist
            if hook is not None:
                hook(int(scene_id), str(content_html), str(title), local_project_id)
                return
            if not _signed_in_user_id():
                return
            from sync.browser_scene_sync import schedule_browser_scene_mirror

            schedule_browser_scene_mirror(
                int(scene_id),
                str(content_html),
                str(title),
                local_project_id,
            )
        except Exception:  # noqa: BLE001 — manuscript save already succeeded
            pass

    def write_scene_content(
        self,
        connection: sqlite3.Connection,
        scene_id: int,
        content: str,
        save_note: str = "문서 가져오기",
    ) -> dict:
        """Shared body write for import, proof apply, and phone-draft merge."""
        repo = self.repository_factory(connection)
        return repo.save_new_revision(
            scene_id,
            str(content),
            0,
            save_note=save_note,
            word_count=self.word_count(str(content)),
        )

    def merge_mobile_draft(
        self,
        scene_id: int,
        content: str,
        *,
        save_note: str = "폰 초안 반영",
        connection: sqlite3.Connection | None = None,
    ) -> dict:
        """Apply a phone draft as a new current revision and bump ``row_version``."""

        def run(conn: sqlite3.Connection) -> dict:
            repo = self.repository_factory(conn)
            if repo.get_scene_meta(scene_id) is None:
                raise ValueError("씬을 찾을 수 없습니다.")
            self.write_scene_content(conn, scene_id, content, save_note=save_note)
            bumped = repo.bump_row_version(scene_id)
            return {
                "ok": True,
                "local_scene_id": int(scene_id),
                "row_version": int(bumped["row_version"]),
            }

        if connection is not None:
            return run(connection)
        with self.database() as conn:
            return run(conn)
