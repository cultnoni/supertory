"""Snapshot reading invites: chosen scenes uploaded to Supabase for beta readers.

Links freeze manuscript at create time (not live sync). Writer-only comment
paragraphs are stripped via ``plain_text_from_content`` before upload.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from env_loader import get_env
from sync.supabase_client import get_current_user, get_supabase_client

DEFAULT_PUBLIC_BASE = "https://supertory.com"
INVITE_TTL_DAYS = 30
TOKEN_BYTES = 24


class ReadingInviteError(Exception):
    """User-facing invite failure with an HTTP-ish status hint."""

    def __init__(self, message: str, *, status: str = "bad_request") -> None:
        super().__init__(message)
        self.status = status


def public_read_url(token: str) -> str:
    base = (get_env("READING_INVITE_PUBLIC_BASE") or DEFAULT_PUBLIC_BASE).rstrip("/")
    return f"{base}/read/{token}"


def new_invite_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def display_status(row: dict[str, Any], *, now: datetime | None = None) -> str:
    if str(row.get("status") or "").strip() == "revoked":
        return "revoked"
    expires = _parse_dt(row.get("expires_at"))
    stamp = now or _utc_now()
    if expires is not None and expires <= stamp:
        return "expired"
    return "active"


def _rows(result: Any) -> list[dict[str, Any]]:
    data = getattr(result, "data", None)
    if not data:
        return []
    if isinstance(data, dict):
        return [data]
    return [row for row in data if isinstance(row, dict)]


def _first(result: Any) -> dict[str, Any] | None:
    rows = _rows(result)
    return rows[0] if rows else None


def _require_user_and_client(
    *,
    user: dict[str, Any] | None = None,
    client: Any | None = None,
) -> tuple[dict[str, Any], Any]:
    current = user if user is not None else get_current_user()
    user_id = str((current or {}).get("id") or "").strip()
    if not user_id:
        raise ReadingInviteError(
            "로그인해 주세요. 읽기 권한 초대는 수퍼토리 계정이 필요합니다.",
            status="auth",
        )
    supabase = client if client is not None else get_supabase_client()
    if supabase is None:
        raise ReadingInviteError(
            "클라우드 연결이 설정되지 않았습니다.",
            status="sync",
        )
    return {"id": user_id, **(current or {})}, supabase


def _supabase_fail(error: Exception, fallback: str) -> ReadingInviteError:
    text = str(error or "")
    if "reading_invites" in text and "schema cache" in text:
        return ReadingInviteError(
            "읽기 초대 테이블이 아직 클라우드에 없어요. "
            "supabase/migrations/20260831090000_reading_invites.sql 을 "
            "Supabase SQL 에디터에서 실행해 주세요.",
            status="server",
        )
    return ReadingInviteError(f"{fallback}: {error}", status="server")


def serialize_invite(row: dict[str, Any], *, comment_count: int = 0) -> dict[str, Any]:
    token = str(row.get("token") or "")
    return {
        "id": str(row.get("id") or ""),
        "token": token,
        "title": str(row.get("title") or ""),
        "status": str(row.get("status") or "active"),
        "display_status": display_status(row),
        "created_at": row.get("created_at"),
        "expires_at": row.get("expires_at"),
        "project_local_id": row.get("project_local_id"),
        "public_url": public_read_url(token) if token else "",
        "comment_count": int(comment_count or 0),
    }


def create_invite(
    *,
    project_id: int,
    title: str,
    scenes: list[dict[str, Any]],
    user: dict[str, Any] | None = None,
    client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not scenes:
        raise ReadingInviteError("선택한 화가 없습니다.")
    owner, supabase = _require_user_and_client(user=user, client=client)
    stamp = now or _utc_now()
    expires_at = (stamp + timedelta(days=INVITE_TTL_DAYS)).isoformat()
    created_at = stamp.isoformat()
    invite_id = str(uuid.uuid4())
    token = new_invite_token()
    payload = {
        "id": invite_id,
        "user_id": owner["id"],
        "token": token,
        "project_local_id": int(project_id),
        "title": str(title or "").strip() or "제목 없음",
        "status": "active",
        "created_at": created_at,
        "expires_at": expires_at,
    }
    try:
        inserted = supabase.table("reading_invites").insert(payload).execute()
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "초대 링크를 만들지 못했습니다") from error
    row = _first(inserted) or payload
    scene_rows = []
    for index, scene in enumerate(scenes):
        scene_rows.append(
            {
                "id": str(uuid.uuid4()),
                "invite_id": str(row.get("id") or invite_id),
                "order_index": int(scene.get("order_index", index)),
                "scene_title": str(scene.get("scene_title") or "").strip() or f"{index + 1}화",
                "content_snapshot": str(scene.get("content_snapshot") or ""),
                "local_scene_id": scene.get("local_scene_id"),
            }
        )
    try:
        supabase.table("reading_invite_scenes").insert(scene_rows).execute()
    except Exception as error:  # noqa: BLE001
        try:
            supabase.table("reading_invites").update({"status": "revoked"}).eq(
                "id", str(row.get("id") or invite_id)
            ).eq("user_id", owner["id"]).execute()
        except Exception:
            pass
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "화 스냅샷을 올리지 못했습니다") from error
    return serialize_invite(row)


def list_invites(
    *,
    project_id: int,
    user: dict[str, Any] | None = None,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    owner, supabase = _require_user_and_client(user=user, client=client)
    try:
        found = (
            supabase.table("reading_invites")
            .select("id, token, project_local_id, title, status, created_at, expires_at, user_id")
            .eq("user_id", owner["id"])
            .eq("project_local_id", int(project_id))
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "링크 목록을 불러오지 못했습니다") from error
    invites = _rows(found)
    counts: dict[str, int] = {}
    invite_ids = [str(row.get("id") or "") for row in invites if row.get("id")]
    if invite_ids:
        try:
            comments = (
                supabase.table("reading_invite_comments")
                .select("id, invite_id")
                .in_("invite_id", invite_ids)
                .execute()
            )
        except Exception as error:  # noqa: BLE001
            raise _supabase_fail(error, "링크 목록을 불러오지 못했습니다") from error
        for row in _rows(comments):
            key = str(row.get("invite_id") or "")
            counts[key] = counts.get(key, 0) + 1
    return [serialize_invite(row, comment_count=counts.get(str(row.get("id") or ""), 0)) for row in invites]


def revoke_invite(
    *,
    invite_id: str,
    user: dict[str, Any] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    owner, supabase = _require_user_and_client(user=user, client=client)
    iid = str(invite_id or "").strip()
    if not iid:
        raise ReadingInviteError("초대 링크를 찾을 수 없습니다.", status="not_found")
    try:
        updated = (
            supabase.table("reading_invites")
            .update({"status": "revoked"})
            .eq("id", iid)
            .eq("user_id", owner["id"])
            .execute()
        )
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "링크를 끄지 못했습니다") from error
    row = _first(updated)
    if not row:
        raise ReadingInviteError("초대 링크를 찾을 수 없습니다.", status="not_found")
    return serialize_invite(row)


def _comment_author(row: dict[str, Any]) -> str:
    name = str(
        row.get("author_name")
        or row.get("display_name")
        or row.get("name")
        or ""
    ).strip()
    return name


def _comment_body(row: dict[str, Any]) -> str:
    return str(row.get("content") or row.get("body") or row.get("text") or "").strip()


def list_invite_comments(
    *,
    invite_id: str,
    user: dict[str, Any] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    owner, supabase = _require_user_and_client(user=user, client=client)
    iid = str(invite_id or "").strip()
    try:
        invite = _first(
            supabase.table("reading_invites")
            .select("id, token, project_local_id, title, status, created_at, expires_at, user_id")
            .eq("id", iid)
            .eq("user_id", owner["id"])
            .limit(1)
            .execute()
        )
        scenes = _rows(
            supabase.table("reading_invite_scenes")
            .select("id, invite_id, order_index, scene_title, local_scene_id")
            .eq("invite_id", iid)
            .order("order_index")
            .execute()
        )
        comments = _rows(
            supabase.table("reading_invite_comments")
            .select("id, invite_id, invite_scene_id, author_name, content, created_at")
            .eq("invite_id", iid)
            .order("created_at")
            .execute()
        )
    except ReadingInviteError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "피드백을 불러오지 못했습니다") from error
    if not invite:
        raise ReadingInviteError("초대 링크를 찾을 수 없습니다.", status="not_found")
    by_scene: dict[str, list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []
    for row in comments:
        item = {
            "id": str(row.get("id") or ""),
            "author_name": _comment_author(row),
            "content": _comment_body(row),
            "created_at": row.get("created_at"),
            "invite_scene_id": str(row.get("invite_scene_id") or "") or None,
        }
        scene_key = str(row.get("invite_scene_id") or "")
        if scene_key:
            by_scene.setdefault(scene_key, []).append(item)
        else:
            ungrouped.append(item)
    grouped = []
    for scene in scenes:
        sid = str(scene.get("id") or "")
        grouped.append(
            {
                "id": sid,
                "order_index": scene.get("order_index"),
                "scene_title": str(scene.get("scene_title") or ""),
                "local_scene_id": scene.get("local_scene_id"),
                "comments": by_scene.get(sid, []),
            }
        )
    return {
        "invite": serialize_invite(invite, comment_count=len(comments)),
        "scenes": grouped,
        "ungrouped": ungrouped,
    }


def feedback_summary(
    *,
    project_id: int,
    since: str | None = None,
    user: dict[str, Any] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    owner, supabase = _require_user_and_client(user=user, client=client)
    try:
        invites = _rows(
            supabase.table("reading_invites")
            .select("id, title")
            .eq("user_id", owner["id"])
            .eq("project_local_id", int(project_id))
            .execute()
        )
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "피드백을 확인하지 못했습니다") from error
    titles = {
        str(row.get("id") or ""): str(row.get("title") or "").strip()
        for row in invites
        if row.get("id")
    }
    invite_ids = [key for key in titles if key]
    if not invite_ids:
        return {"new_count": 0, "latest_created_at": None, "invite_id": None, "title": ""}
    try:
        comments = _rows(
            supabase.table("reading_invite_comments")
            .select("id, invite_id, created_at")
            .in_("invite_id", invite_ids)
            .execute()
        )
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "피드백을 확인하지 못했습니다") from error
    since_dt = _parse_dt(since)
    latest: datetime | None = None
    latest_new: datetime | None = None
    latest_new_invite_id = ""
    new_count = 0
    for row in comments:
        created = _parse_dt(row.get("created_at"))
        if created is None:
            continue
        if latest is None or created > latest:
            latest = created
        if since_dt is None or created > since_dt:
            new_count += 1
            if latest_new is None or created > latest_new:
                latest_new = created
                latest_new_invite_id = str(row.get("invite_id") or "")
    return {
        "new_count": new_count,
        "latest_created_at": latest.isoformat() if latest else None,
        "invite_id": latest_new_invite_id or None,
        "title": titles.get(latest_new_invite_id, ""),
    }
