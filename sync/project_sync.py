"""로컬 작품 ID(정수)를 Supabase projects UUID에 매핑하고, project_status로 체크아웃/체크인한다."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from typing import Any

import folder_tree
from sync.auth_helpers import _auth_user_id
from sync.device import get_desktop_device_id
from sync.supabase_client import get_supabase_client

_SCENE_SNAPSHOT_BATCH_SIZE = 30


def _warn(message: str) -> None:
    print(f"경고: {message}", file=sys.stderr)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_project_mirror(local_project_id: int, title: str) -> str | None:
    """Map a local integer project id to the remote projects.id UUID.

    Looks up ``local_project_id``. Inserts a new UUID row when missing,
    otherwise refreshes title/updated_at. Returns the remote UUID, or None
    when sync is off or the call fails.
    """
    client = get_supabase_client()
    if client is None:
        return None

    try:
        local_id = int(local_project_id)
        now = _now()
        query = (
            client.table("projects")
            .select("id")
            .eq("local_project_id", local_id)
        )
        user_id = _auth_user_id(client)
        if user_id:
            query = query.eq("user_id", user_id)
        found = query.limit(1).execute()
        rows = getattr(found, "data", None) or []
        if rows:
            remote_id = str(rows[0].get("id") or "").strip()
            if not remote_id:
                return None
            patch = {"updated_at": now}
            if str(title or "").strip():
                patch["title"] = str(title)
            (
                client.table("projects")
                .update(patch)
                .eq("id", remote_id)
                .execute()
            )
            return remote_id

        remote_id = str(uuid.uuid4())
        row = {
            "id": remote_id,
            "local_project_id": local_id,
            "title": str(title or ""),
            "updated_at": now,
        }
        if user_id:
            row["user_id"] = user_id
        client.table("projects").insert(row).execute()
        return remote_id
    except Exception as error:  # noqa: BLE001 — local app must keep running
        _warn(f"프로젝트 미러 동기화에 실패했습니다: {error}")
        return None


def checkout_project(local_project_id: int, title: str) -> None:
    """Mark this desktop as holding the project, after mirroring metadata."""
    if get_supabase_client() is None:
        return

    try:
        remote_id = ensure_project_mirror(local_project_id, title)
        if not remote_id:
            return
        client = get_supabase_client()
        if client is None:
            return
        client.table("project_status").upsert(
            {
                "project_id": remote_id,
                "checked_out_by": get_desktop_device_id(),
                "checked_out_at": _now(),
            }
        ).execute()
    except Exception as error:  # noqa: BLE001 — local app must keep running
        _warn(f"프로젝트 체크아웃에 실패했습니다: {error}")


def _running_app():
    """Live SuperTory module: `app` in tests, `__main__` when launched as app.py."""
    for name in ("app", "__main__"):
        mod = sys.modules.get(name)
        if (
            mod is not None
            and callable(getattr(mod, "database", None))
            and getattr(mod, "SuperToryHandler", None) is not None
        ):
            return mod
    return None


def _folder_path_by_scene_id(forest: list[dict], flatten) -> dict[int, str]:
    """Map scene id → ancestor folder titles joined with '/' (boxes included)."""
    paths: dict[int, str] = {}

    def visit(nodes: list[dict], ancestors: list[str]) -> None:
        for node in nodes or []:
            if not node:
                continue
            title = str(node.get("title") or "").strip()
            path_parts = [*ancestors, title] if title else list(ancestors)
            folder_path = "/".join(path_parts)
            if not bool(node.get("is_box")):
                for scene in flatten(node.get("scenes") or []):
                    try:
                        sid = int(scene.get("id") or 0)
                    except (TypeError, ValueError):
                        continue
                    if sid > 0 and sid not in paths:
                        paths[sid] = folder_path
            visit(node.get("children") or [], path_parts)

    visit(forest, [])
    return paths


def _collect_local_scene_snapshots(
    local_project_id: int,
) -> tuple[str, list[dict[str, Any]]]:
    """Binder DFS scene rows plus current-revision snapshots from the local DB."""
    app_mod = _running_app()
    if app_mod is None:
        return "", []

    local_id = int(local_project_id)
    handler = object.__new__(app_mod.SuperToryHandler)
    with app_mod.database() as connection:
        title_row = connection.execute(
            "SELECT title FROM project WHERE id = ? AND deleted_at IS NULL",
            (local_id,),
        ).fetchone()
        title = str((title_row["title"] if title_row else "") or "")
        ordered = handler._list_scenes_in_binder_order(connection, local_id)
        paths: dict[int, str] = {}
        if folder_tree.folder_table_ready(connection):
            forest = handler._build_folders_tree_from_db(
                connection,
                local_id,
                scenes_rows=handler._load_binder_order_scene_rows(connection, local_id),
            )
            if forest:
                paths = _folder_path_by_scene_id(
                    forest, handler._flatten_scene_tree
                )
        revisions: dict[int, tuple[str, int | None]] = {}
        for row in connection.execute(
            """
            SELECT s.id AS scene_id,
                   COALESCE(v.content_md, '') AS content_md,
                   v.revision_no AS revision_no
            FROM scene s
            LEFT JOIN v_current_scene_revision v ON v.scene_id = s.id
            WHERE s.project_id = ? AND s.deleted_at IS NULL
            """,
            (local_id,),
        ).fetchall():
            raw_rev = row["revision_no"]
            revision_no = None
            if raw_rev is not None and str(raw_rev).strip() != "":
                try:
                    revision_no = int(raw_rev)
                except (TypeError, ValueError):
                    revision_no = None
            revisions[int(row["scene_id"])] = (str(row["content_md"] or ""), revision_no)

    payloads: list[dict[str, Any]] = []
    for scene in ordered:
        try:
            sid = int(scene.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if sid <= 0:
            continue
        rev = revisions.get(sid)
        if rev is not None:
            content, revision_no = rev
        else:
            content = str(scene.get("content_md") or "")
            revision_no = None
        folder_path = paths.get(sid)
        if not folder_path:
            folder_path = str(scene.get("chapter_title") or "").strip()
        payloads.append(
            {
                "local_scene_id": sid,
                "title": str(scene.get("title") or ""),
                "folder_path": folder_path,
                "content_snapshot": content,
                "snapshot_revision_no": revision_no,
            }
        )
    return title, payloads


def _upsert_scene_snapshots(client: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    batch = max(1, int(_SCENE_SNAPSHOT_BATCH_SIZE))
    for index in range(0, len(rows), batch):
        chunk = rows[index : index + batch]
        (
            client.table("scenes")
            .upsert(chunk, on_conflict="project_id,local_scene_id")
            .execute()
        )


def sync_scenes_snapshot(local_project_id: int) -> None:
    """Mirror each scene's current manuscript snapshot to Supabase ``scenes``.

    Intended to run right after ``checkout_project()``. No-ops when sync is off.
    """
    if get_supabase_client() is None:
        return

    try:
        local_id = int(local_project_id)
        title, payloads = _collect_local_scene_snapshots(local_id)
        remote_id = ensure_project_mirror(local_id, title)
        if not remote_id:
            return
        client = get_supabase_client()
        if client is None:
            return
        rows = [{**payload, "project_id": remote_id} for payload in payloads]
        _upsert_scene_snapshots(client, rows)
    except Exception as error:  # noqa: BLE001 — local app must keep running
        _warn(f"회차 스냅샷 동기화에 실패했습니다: {error}")


def fetch_pending_drafts(local_project_id: int) -> list[dict[str, Any]]:
    """Return pending ``scene_drafts`` for this local project.

    Uses ``ensure_project_mirror`` for the remote ``projects.id``. Returns []
    when sync is off or the lookup fails.
    """
    client = get_supabase_client()
    if client is None:
        return []

    try:
        remote_id = ensure_project_mirror(int(local_project_id), "")
        if not remote_id:
            return []
        found = (
            client.table("scene_drafts")
            .select("id, local_scene_id, content, based_on_revision_no, created_at")
            .eq("project_id", remote_id)
            .eq("status", "pending")
            .order("created_at")
            .execute()
        )
        rows = getattr(found, "data", None) or []
        drafts: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            draft_id = row.get("id")
            if draft_id is None or str(draft_id).strip() == "":
                continue
            try:
                local_scene_id = int(row.get("local_scene_id"))
            except (TypeError, ValueError):
                continue
            based = row.get("based_on_revision_no")
            based_no = None
            if based is not None and str(based).strip() != "":
                try:
                    based_no = int(based)
                except (TypeError, ValueError):
                    based_no = None
            drafts.append(
                {
                    "draft_id": draft_id,
                    "local_scene_id": local_scene_id,
                    "content": str(row.get("content") or ""),
                    "based_on_revision_no": based_no,
                    "created_at": row.get("created_at"),
                }
            )
        return drafts
    except Exception as error:  # noqa: BLE001 — local app must keep running
        _warn(f"모바일 초안을 불러오지 못했습니다: {error}")
        return []


def mark_draft_merged(draft_id: object) -> None:
    """Set ``scene_drafts.status`` to ``merged``. No-ops when sync is off."""
    client = get_supabase_client()
    if client is None:
        return
    if draft_id is None or str(draft_id).strip() == "":
        return

    try:
        (
            client.table("scene_drafts")
            .update({"status": "merged"})
            .eq("id", draft_id)
            .execute()
        )
    except Exception as error:  # noqa: BLE001 — local app must keep running
        _warn(f"초안 상태를 갱신하지 못했습니다: {error}")


def checkin_project(local_project_id: int) -> None:
    """Release the remote checkout lock. Missing rows are ignored."""
    client = get_supabase_client()
    if client is None:
        return

    try:
        remote_id = ensure_project_mirror(local_project_id, "")
        if not remote_id:
            return
        found = (
            client.table("project_status")
            .select("project_id")
            .eq("project_id", remote_id)
            .limit(1)
            .execute()
        )
        rows = getattr(found, "data", None) or []
        if not rows:
            return
        (
            client.table("project_status")
            .update({"checked_out_by": None, "checked_out_at": None})
            .eq("project_id", remote_id)
            .execute()
        )
    except Exception as error:  # noqa: BLE001 — local app must keep running
        _warn(f"프로젝트 체크인에 실패했습니다: {error}")
