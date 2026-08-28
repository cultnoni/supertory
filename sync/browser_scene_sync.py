"""Mirror a desktop scene save into Supabase ``browser_scenes``.

Local SQLite is already committed before this runs. Failures are swallowed so
a network/sync error cannot roll back the manuscript save.
"""

from __future__ import annotations

import sys
import uuid
from threading import Thread
from typing import Any, Callable

from services.conflict_resolution_service import (
    MissingUserSettings,
    resolve_write,
)
from sync.supabase_client import get_current_user, get_supabase_client

SOURCE_TABLE = "browser_scenes"
DEVICE_TYPE = "desktop"

_UNSET = object()
_last_known_updated_at: dict[tuple[str, int], str] = {}


def _warn(message: str) -> None:
    print(f"경고: {message}", file=sys.stderr)


def reset_browser_scene_mirror_cache() -> None:
    """Test helper: forget last successful mirror timestamps."""
    _last_known_updated_at.clear()


def _as_int(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_content(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(row.get("title") or ""),
        "content_html": str(row.get("content_html") or ""),
        "local_scene_id": _as_int(row.get("local_scene_id")),
        "local_project_id": _as_int(row.get("local_project_id")),
        "row_version": _as_int(row.get("row_version")) or 1,
    }


def _next_row_version(row: dict[str, Any] | None) -> int:
    if not row:
        return 1
    current = _as_int(row.get("row_version")) or 1
    return max(1, current + 1)


class BrowserScenesConflictStore:
    """``ConflictStore`` backed by ``browser_scenes`` + ``document_conflict_backups``."""

    def __init__(self, client: Any, user_id: str) -> None:
        self.client = client
        self.user_id = str(user_id)

    def get_user_settings(self, user_id: str) -> dict[str, Any] | None:
        owner = str(user_id or "").strip()
        if not owner:
            return None
        found = (
            self.client.table("user_settings")
            .select("user_id, primary_device_type, conflict_policy_agreed_at")
            .eq("user_id", owner)
            .limit(1)
            .execute()
        )
        rows = getattr(found, "data", None) or []
        if not rows or not isinstance(rows[0], dict):
            return None
        primary = str(rows[0].get("primary_device_type") or "").strip()
        if not primary:
            return None
        return {"primary_device_type": primary}

    def get_record(self, source_table: str, record_id: str) -> dict[str, Any] | None:
        if str(source_table or "").strip() != SOURCE_TABLE:
            return None
        row_id = str(record_id or "").strip()
        if not row_id:
            return None
        found = (
            self.client.table(SOURCE_TABLE)
            .select(
                "id, title, content_html, row_version, updated_at, "
                "local_scene_id, local_project_id, user_id"
            )
            .eq("id", row_id)
            .limit(1)
            .execute()
        )
        rows = getattr(found, "data", None) or []
        if not rows or not isinstance(rows[0], dict):
            return None
        row = rows[0]
        return {
            "updated_at": row.get("updated_at"),
            "content": _row_content(row),
        }

    def save_record(
        self,
        source_table: str,
        record_id: str,
        content: Any,
        updated_at: str,
        device_type: str,
    ) -> None:
        del device_type
        if str(source_table or "").strip() != SOURCE_TABLE:
            return
        payload = content if isinstance(content, dict) else {"content_html": str(content or "")}
        row = {
            "id": str(record_id),
            "user_id": self.user_id,
            "title": str(payload.get("title") or ""),
            "content_html": str(payload.get("content_html") or ""),
            "row_version": _as_int(payload.get("row_version")) or 1,
            "local_scene_id": _as_int(payload.get("local_scene_id")),
            "local_project_id": _as_int(payload.get("local_project_id")),
            "updated_at": updated_at,
        }
        (
            self.client.table(SOURCE_TABLE)
            .upsert(row, on_conflict="id")
            .execute()
        )

    def insert_conflict_backup(self, payload: dict[str, Any]) -> dict[str, Any]:
        saved = (
            self.client.table("document_conflict_backups")
            .insert(payload)
            .execute()
        )
        rows = getattr(saved, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return rows[0]
        return dict(payload)


def _find_by_local_scene_id(
    client: Any, user_id: str, local_scene_id: int
) -> dict[str, Any] | None:
    found = (
        client.table(SOURCE_TABLE)
        .select(
            "id, title, content_html, row_version, updated_at, "
            "local_scene_id, local_project_id, user_id"
        )
        .eq("user_id", user_id)
        .eq("local_scene_id", int(local_scene_id))
        .limit(1)
        .execute()
    )
    rows = getattr(found, "data", None) or []
    if not rows or not isinstance(rows[0], dict):
        return None
    return rows[0]


def mirror_desktop_scene(
    local_scene_id: int,
    content_html: str,
    title: str,
    local_project_id: int,
    *,
    user_id: str | None = None,
    last_known_updated_at: Any = _UNSET,
    store: BrowserScenesConflictStore | None = None,
    client: Any | None = None,
    resolve_write_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Find-or-create ``browser_scenes`` via ``resolve_write``. Returns the result or None."""
    owner = str(user_id or "").strip()
    if not owner:
        current = get_current_user()
        owner = str((current or {}).get("id") or "").strip()
    if not owner:
        return None

    supabase = client if client is not None else get_supabase_client()
    if supabase is None:
        return None

    scene_id = int(local_scene_id)
    project_id = int(local_project_id)
    existing = _find_by_local_scene_id(supabase, owner, scene_id)
    if existing:
        record_id = str(existing.get("id") or "").strip() or str(uuid.uuid4())
        cache_key = (owner, scene_id)
        if last_known_updated_at is _UNSET:
            last_known = _last_known_updated_at.get(cache_key)
            if last_known is None:
                last_known = existing.get("updated_at")
        else:
            last_known = last_known_updated_at
        row_version = _next_row_version(existing)
    else:
        record_id = str(uuid.uuid4())
        last_known = None if last_known_updated_at is _UNSET else last_known_updated_at
        row_version = 1

    if not record_id:
        return None

    conflict_store = store or BrowserScenesConflictStore(supabase, owner)
    new_content = {
        "title": str(title or ""),
        "content_html": str(content_html or ""),
        "local_scene_id": scene_id,
        "local_project_id": project_id,
        "row_version": row_version,
    }
    writer = resolve_write_fn or resolve_write
    try:
        result = writer(
            owner,
            SOURCE_TABLE,
            record_id,
            DEVICE_TYPE,
            new_content,
            last_known,
            conflict_store,
        )
    except MissingUserSettings:
        _warn("우선 기기를 먼저 선택해 주세요. 브라우저 미러를 건너뜁니다.")
        return None

    if result.get("saved"):
        refreshed = conflict_store.get_record(SOURCE_TABLE, record_id)
        stamp = (refreshed or {}).get("updated_at") or result.get("updated_at")
        if stamp:
            _last_known_updated_at[(owner, scene_id)] = str(stamp)
    return result


def schedule_browser_scene_mirror(
    local_scene_id: int,
    content_html: str,
    title: str,
    local_project_id: int,
) -> None:
    """Fire-and-forget mirror. No-ops when logged out; never raises."""
    try:
        user = get_current_user()
        if not user or not str(user.get("id") or "").strip():
            return
        worker = Thread(
            target=_browser_scene_mirror_worker,
            args=(
                int(local_scene_id),
                str(content_html or ""),
                str(title or ""),
                int(local_project_id),
                str(user["id"]),
            ),
            daemon=True,
            name=f"browser-scene-mirror-{local_scene_id}",
        )
        worker.start()
    except Exception:  # noqa: BLE001 — local save already succeeded
        pass


def _browser_scene_mirror_worker(
    local_scene_id: int,
    content_html: str,
    title: str,
    local_project_id: int,
    user_id: str,
) -> None:
    try:
        mirror_desktop_scene(
            local_scene_id,
            content_html,
            title,
            local_project_id,
            user_id=user_id,
        )
    except Exception as error:  # noqa: BLE001 — local app must keep running
        _warn(f"브라우저 씬 미러 동기화에 실패했습니다: {error}")
