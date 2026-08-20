"""로컬 작품 ID(정수)를 Supabase projects UUID에 매핑하고, project_status로 체크아웃/체크인한다."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

from sync.device import get_desktop_device_id
from sync.supabase_client import get_supabase_client


def _warn(message: str) -> None:
    print(f"경고: {message}", file=sys.stderr)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _auth_user_id(client: object) -> str | None:
    try:
        session_user = getattr(getattr(client, "auth", None), "get_user", None)
        if callable(session_user):
            result = session_user()
            user = getattr(result, "user", None) or (
                result.get("user") if isinstance(result, dict) else None
            )
            user_id = getattr(user, "id", None) if user is not None else None
            if user_id:
                return str(user_id)
    except Exception:  # noqa: BLE001
        return None
    return None


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
