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


def is_deleted_invite(row: dict[str, Any] | None) -> bool:
    return str((row or {}).get("status") or "").strip() == "deleted"


def display_status(row: dict[str, Any], *, now: datetime | None = None) -> str:
    status = str(row.get("status") or "").strip()
    if status == "deleted":
        return "deleted"
    if status == "revoked":
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


def _schema_looks_unmigrated(error: Exception) -> bool:
    text = str(error or "")
    lower = text.lower()
    return "reading_invite" in lower and (
        "schema cache" in lower
        or "does not exist" in lower
        or "42703" in text
        or "42p01" in lower
    )


def _supabase_fail(error: Exception, fallback: str) -> ReadingInviteError:
    if _schema_looks_unmigrated(error):
        return ReadingInviteError(
            "읽기 초대 클라우드 스키마가 아직 최신이 아니에요. "
            "supabase/migrations/20260831090000_reading_invites.sql, "
            "supabase/migrations/20260903020000_reading_invite_edits.sql, "
            "supabase/migrations/20260903040000_reading_invite_deleted.sql 을 "
            "Supabase SQL 에디터에서 실행해 주세요.",
            status="server",
        )
    return ReadingInviteError(f"{fallback}: {error}", status="server")


def normalize_permission(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "edit" if text in {"edit", "write", "editor"} else "read"


def _optional_message(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def serialize_invite(
    row: dict[str, Any],
    *,
    comment_count: int = 0,
    edit_count: int = 0,
) -> dict[str, Any]:
    token = str(row.get("token") or "")
    permission = normalize_permission(row.get("permission"))
    return {
        "id": str(row.get("id") or ""),
        "token": token,
        "title": str(row.get("title") or ""),
        "status": str(row.get("status") or "active"),
        "display_status": display_status(row),
        "permission": permission,
        "message": _optional_message(row.get("message")),
        "created_at": row.get("created_at"),
        "expires_at": row.get("expires_at"),
        "project_local_id": row.get("project_local_id"),
        "public_url": public_read_url(token) if token else "",
        "comment_count": int(comment_count or 0),
        "edit_count": int(edit_count or 0),
    }


def create_invite(
    *,
    project_id: int,
    title: str,
    scenes: list[dict[str, Any]],
    user: dict[str, Any] | None = None,
    client: Any | None = None,
    now: datetime | None = None,
    permission: str | None = None,
    message: str | None = None,
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
        "permission": normalize_permission(permission),
        "message": _optional_message(message),
        "created_at": created_at,
        "expires_at": expires_at,
    }
    try:
        inserted = supabase.table("reading_invites").insert(payload).execute()
    except Exception as error:  # noqa: BLE001
        if (
            _schema_looks_unmigrated(error)
            and payload.get("permission") == "read"
            and not payload.get("message")
        ):
            legacy = {key: value for key, value in payload.items() if key not in {"permission", "message"}}
            try:
                inserted = supabase.table("reading_invites").insert(legacy).execute()
            except Exception as legacy_error:  # noqa: BLE001
                raise _supabase_fail(legacy_error, "초대 링크를 만들지 못했습니다") from legacy_error
        else:
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
            .select(
                "id, token, project_local_id, title, status, permission, message, "
                "created_at, expires_at, user_id"
            )
            .eq("user_id", owner["id"])
            .eq("project_local_id", int(project_id))
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as error:  # noqa: BLE001
        if not _schema_looks_unmigrated(error):
            raise _supabase_fail(error, "링크 목록을 불러오지 못했습니다") from error
        try:
            found = (
                supabase.table("reading_invites")
                .select(
                    "id, token, project_local_id, title, status, "
                    "created_at, expires_at, user_id"
                )
                .eq("user_id", owner["id"])
                .eq("project_local_id", int(project_id))
                .order("created_at", desc=True)
                .execute()
            )
        except Exception as legacy_error:  # noqa: BLE001
            raise _supabase_fail(legacy_error, "링크 목록을 불러오지 못했습니다") from legacy_error
    invites = [row for row in _rows(found) if not is_deleted_invite(row)]
    counts: dict[str, int] = {}
    edit_counts: dict[str, int] = {}
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
        try:
            edits = (
                supabase.table("reading_invite_edits")
                .select("id, invite_id")
                .in_("invite_id", invite_ids)
                .execute()
            )
            for row in _rows(edits):
                key = str(row.get("invite_id") or "")
                edit_counts[key] = edit_counts.get(key, 0) + 1
        except Exception:
            edit_counts = {}
    return [
        serialize_invite(
            row,
            comment_count=counts.get(str(row.get("id") or ""), 0),
            edit_count=edit_counts.get(str(row.get("id") or ""), 0),
        )
        for row in invites
    ]


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


def _status_rejects_deleted(error: Exception) -> bool:
    text = str(error or "").lower()
    return "check" in text and ("status" in text or "23514" in text)


def delete_invite(
    *,
    invite_id: str,
    user: dict[str, Any] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Soft-delete an invite (status=deleted). Falls back to hard delete if the
    live check constraint has not been migrated yet."""
    owner, supabase = _require_user_and_client(user=user, client=client)
    iid = str(invite_id or "").strip()
    if not iid:
        raise ReadingInviteError("초대 링크를 찾을 수 없습니다.", status="not_found")
    try:
        updated = (
            supabase.table("reading_invites")
            .update({"status": "deleted"})
            .eq("id", iid)
            .eq("user_id", owner["id"])
            .execute()
        )
    except Exception as error:  # noqa: BLE001
        if not _status_rejects_deleted(error):
            raise _supabase_fail(error, "링크를 삭제하지 못했습니다") from error
        try:
            removed = (
                supabase.table("reading_invites")
                .delete()
                .eq("id", iid)
                .eq("user_id", owner["id"])
                .execute()
            )
        except Exception as delete_error:  # noqa: BLE001
            raise _supabase_fail(delete_error, "링크를 삭제하지 못했습니다") from delete_error
        row = _first(removed)
        if not row:
            raise ReadingInviteError("초대 링크를 찾을 수 없습니다.", status="not_found")
        row = {**row, "status": "deleted"}
        return serialize_invite(row)
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
        try:
            invite = _first(
                supabase.table("reading_invites")
                .select(
                    "id, token, project_local_id, title, status, permission, message, "
                    "created_at, expires_at, user_id"
                )
                .eq("id", iid)
                .eq("user_id", owner["id"])
                .limit(1)
                .execute()
            )
        except Exception as error:  # noqa: BLE001
            if not _schema_looks_unmigrated(error):
                raise
            invite = _first(
                supabase.table("reading_invites")
                .select(
                    "id, token, project_local_id, title, status, "
                    "created_at, expires_at, user_id"
                )
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
    if not invite or is_deleted_invite(invite):
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
            .select("id, title, status")
            .eq("user_id", owner["id"])
            .eq("project_local_id", int(project_id))
            .execute()
        )
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "피드백을 확인하지 못했습니다") from error
    titles = {
        str(row.get("id") or ""): str(row.get("title") or "").strip()
        for row in invites
        if row.get("id") and not is_deleted_invite(row)
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


def ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    left_a, right_a = int(a_start), int(a_end)
    left_b, right_b = int(b_start), int(b_end)
    if right_a < left_a:
        left_a, right_a = right_a, left_a
    if right_b < left_b:
        left_b, right_b = right_b, left_b
    if left_a == right_a and left_b == right_b:
        return left_a == left_b
    return left_a < right_b and left_b < right_a


def apply_fragment_to_text(
    text: str,
    *,
    original_fragment: str,
    replacement_fragment: str,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> str:
    """Apply one suggested change from the back-safe offset or by unique fragment."""
    source = text if text is not None else ""
    original = original_fragment if original_fragment is not None else ""
    replacement = replacement_fragment if replacement_fragment is not None else ""
    if start_offset is not None and end_offset is not None:
        start = max(0, int(start_offset))
        end = max(start, int(end_offset))
        if start <= len(source) and end <= len(source):
            slice_text = source[start:end]
            if slice_text == original or (not original and start == end):
                return source[:start] + replacement + source[end:]
    if original:
        at = source.find(original)
        if at >= 0:
            return source[:at] + replacement + source[at + len(original):]
        raise ReadingInviteError("원문에서 해당 조각을 찾지 못했어요.")
    raise ReadingInviteError("원문에서 해당 조각을 찾지 못했어요.")


def apply_changes_from_back(text: str, changes: list[dict[str, Any]]) -> str:
    """Apply pending changes starting at the highest start_offset."""
    working = text if text is not None else ""
    ordered = sorted(
        changes,
        key=lambda row: (int(row.get("start_offset") or 0), int(row.get("change_index") or 0)),
        reverse=True,
    )
    for row in ordered:
        working = apply_fragment_to_text(
            working,
            original_fragment=str(row.get("original_fragment") or ""),
            replacement_fragment=str(row.get("replacement_fragment") or ""),
            start_offset=row.get("start_offset"),
            end_offset=row.get("end_offset"),
        )
    return working


def _looks_like_html(text: str) -> bool:
    return bool(text) and "<" in text and ">" in text


def apply_fragment_to_content(
    content: str,
    *,
    original_fragment: str,
    replacement_fragment: str,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> str:
    source = content if content is not None else ""
    original = original_fragment if original_fragment is not None else ""
    replacement = replacement_fragment if replacement_fragment is not None else ""
    if original and original in source:
        return source.replace(original, replacement, 1)
    if not _looks_like_html(source):
        return apply_fragment_to_text(
            source,
            original_fragment=original,
            replacement_fragment=replacement,
            start_offset=start_offset,
            end_offset=end_offset,
        )
    from html import escape as html_escape

    if original:
        escaped_original = html_escape(original, quote=False)
        if escaped_original in source:
            return source.replace(escaped_original, html_escape(replacement, quote=False), 1)
    raise ReadingInviteError("원고에서 해당 조각을 찾지 못했어요. 원문이 바뀌었을 수 있어요.")


def _change_payload(row: dict[str, Any], *, display_status: str | None = None) -> dict[str, Any]:
    status = str(row.get("status") or "pending")
    return {
        "id": str(row.get("id") or ""),
        "edit_id": str(row.get("edit_id") or ""),
        "change_index": int(row.get("change_index") or 0),
        "original_fragment": str(row.get("original_fragment") or ""),
        "replacement_fragment": str(row.get("replacement_fragment") or ""),
        "start_offset": int(row.get("start_offset") or 0),
        "end_offset": int(row.get("end_offset") or 0),
        "status": status,
        "display_status": display_status or status,
        "reviewed_at": row.get("reviewed_at"),
    }


def _display_status_for_change(
    change: dict[str, Any],
    accepted: list[dict[str, Any]],
) -> str:
    status = str(change.get("status") or "pending")
    if status != "pending":
        return status
    start = int(change.get("start_offset") or 0)
    end = int(change.get("end_offset") or 0)
    for other in accepted:
        if ranges_overlap(
            start,
            end,
            int(other.get("start_offset") or 0),
            int(other.get("end_offset") or 0),
        ):
            return "conflict"
    return "pending"


def list_invite_edits(
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
            .select(
                "id, token, project_local_id, title, status, permission, message, "
                "created_at, expires_at, user_id"
            )
            .eq("id", iid)
            .eq("user_id", owner["id"])
            .limit(1)
            .execute()
        )
        scenes = _rows(
            supabase.table("reading_invite_scenes")
            .select("id, invite_id, order_index, scene_title, local_scene_id, content_snapshot")
            .eq("invite_id", iid)
            .order("order_index")
            .execute()
        )
        edits = _rows(
            supabase.table("reading_invite_edits")
            .select("id, invite_id, scene_id, commenter_name, edited_text_snapshot, created_at")
            .eq("invite_id", iid)
            .order("created_at")
            .execute()
        )
        edit_ids = [str(row.get("id") or "") for row in edits if row.get("id")]
        changes: list[dict[str, Any]] = []
        if edit_ids:
            changes = _rows(
                supabase.table("reading_invite_edit_changes")
                .select(
                    "id, edit_id, change_index, original_fragment, replacement_fragment, "
                    "start_offset, end_offset, status, reviewed_at"
                )
                .in_("edit_id", edit_ids)
                .order("change_index")
                .execute()
            )
    except ReadingInviteError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "수정 제안을 불러오지 못했습니다") from error
    if not invite or is_deleted_invite(invite):
        raise ReadingInviteError("초대 링크를 찾을 수 없습니다.", status="not_found")

    changes_by_edit: dict[str, list[dict[str, Any]]] = {}
    accepted_by_scene: dict[str, list[dict[str, Any]]] = {}
    edit_scene: dict[str, str] = {
        str(row.get("id") or ""): str(row.get("scene_id") or "")
        for row in edits
    }
    for row in changes:
        edit_key = str(row.get("edit_id") or "")
        changes_by_edit.setdefault(edit_key, []).append(row)
        if str(row.get("status") or "") == "accepted":
            scene_key = edit_scene.get(edit_key, "")
            accepted_by_scene.setdefault(scene_key, []).append(row)

    grouped = []
    for scene in scenes:
        sid = str(scene.get("id") or "")
        submissions = []
        for edit in edits:
            if str(edit.get("scene_id") or "") != sid:
                continue
            edit_id = str(edit.get("id") or "")
            raw_changes = changes_by_edit.get(edit_id, [])
            accepted = accepted_by_scene.get(sid, [])
            items = []
            for change in raw_changes:
                display = _display_status_for_change(change, accepted)
                items.append(_change_payload(change, display_status=display))
            items.sort(key=lambda item: item["change_index"])
            submissions.append(
                {
                    "id": edit_id,
                    "commenter_name": str(edit.get("commenter_name") or "").strip() or None,
                    "created_at": edit.get("created_at"),
                    "edited_text_snapshot": str(edit.get("edited_text_snapshot") or ""),
                    "changes": items,
                }
            )
        grouped.append(
            {
                "id": sid,
                "order_index": scene.get("order_index"),
                "scene_title": str(scene.get("scene_title") or ""),
                "local_scene_id": scene.get("local_scene_id"),
                "content_snapshot": str(scene.get("content_snapshot") or ""),
                "submissions": submissions,
            }
        )
    return {
        "invite": serialize_invite(invite, edit_count=len(edits)),
        "scenes": grouped,
    }


def load_edit_change_for_review(
    *,
    change_id: str,
    user: dict[str, Any] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    owner, supabase = _require_user_and_client(user=user, client=client)
    cid = str(change_id or "").strip()
    if not cid:
        raise ReadingInviteError("수정 조각을 찾을 수 없습니다.", status="not_found")
    try:
        change = _first(
            supabase.table("reading_invite_edit_changes")
            .select(
                "id, edit_id, change_index, original_fragment, replacement_fragment, "
                "start_offset, end_offset, status, reviewed_at"
            )
            .eq("id", cid)
            .limit(1)
            .execute()
        )
        if not change:
            raise ReadingInviteError("수정 조각을 찾을 수 없습니다.", status="not_found")
        edit = _first(
            supabase.table("reading_invite_edits")
            .select("id, invite_id, scene_id, commenter_name, created_at")
            .eq("id", str(change.get("edit_id") or ""))
            .limit(1)
            .execute()
        )
        if not edit:
            raise ReadingInviteError("수정 조각을 찾을 수 없습니다.", status="not_found")
        invite = _first(
            supabase.table("reading_invites")
            .select(
                "id, token, project_local_id, title, status, permission, message, "
                "created_at, expires_at, user_id"
            )
            .eq("id", str(edit.get("invite_id") or ""))
            .eq("user_id", owner["id"])
            .limit(1)
            .execute()
        )
        if not invite or is_deleted_invite(invite):
            raise ReadingInviteError("수정 조각을 찾을 수 없습니다.", status="not_found")
        scene = _first(
            supabase.table("reading_invite_scenes")
            .select("id, invite_id, order_index, scene_title, local_scene_id, content_snapshot")
            .eq("id", str(edit.get("scene_id") or ""))
            .limit(1)
            .execute()
        )
        sibling_edits = _rows(
            supabase.table("reading_invite_edits")
            .select("id, scene_id")
            .eq("invite_id", str(invite.get("id") or ""))
            .eq("scene_id", str(edit.get("scene_id") or ""))
            .execute()
        )
        sibling_ids = [str(row.get("id") or "") for row in sibling_edits if row.get("id")]
        siblings: list[dict[str, Any]] = []
        if sibling_ids:
            siblings = _rows(
                supabase.table("reading_invite_edit_changes")
                .select(
                    "id, edit_id, change_index, original_fragment, replacement_fragment, "
                    "start_offset, end_offset, status, reviewed_at"
                )
                .in_("edit_id", sibling_ids)
                .execute()
            )
    except ReadingInviteError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "수정 조각을 확인하지 못했습니다") from error
    accepted = [row for row in siblings if str(row.get("status") or "") == "accepted"]
    display = _display_status_for_change(change, accepted)
    return {
        "invite": serialize_invite(invite),
        "scene": {
            "id": str((scene or {}).get("id") or ""),
            "scene_title": str((scene or {}).get("scene_title") or ""),
            "local_scene_id": (scene or {}).get("local_scene_id"),
            "content_snapshot": str((scene or {}).get("content_snapshot") or ""),
        },
        "change": _change_payload(change, display_status=display),
        "siblings": [_change_payload(row) for row in siblings],
    }


def mark_edit_change_status(
    *,
    change_id: str,
    status: str,
    user: dict[str, Any] | None = None,
    client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    owner, supabase = _require_user_and_client(user=user, client=client)
    next_status = str(status or "").strip()
    if next_status not in {"accepted", "rejected"}:
        raise ReadingInviteError("처리할 수 없는 상태입니다.")
    loaded = load_edit_change_for_review(change_id=change_id, user=owner, client=supabase)
    current = str(loaded["change"].get("status") or "pending")
    if current in {"accepted", "rejected"}:
        raise ReadingInviteError("이미 처리한 조각입니다.")
    if next_status == "accepted" and loaded["change"].get("display_status") == "conflict":
        raise ReadingInviteError("겹치는 조각은 수락할 수 없어요. 먼저 거절해 주세요.")
    stamp = (now or _utc_now()).isoformat()
    try:
        updated = (
            supabase.table("reading_invite_edit_changes")
            .update({"status": next_status, "reviewed_at": stamp})
            .eq("id", str(loaded["change"]["id"]))
            .execute()
        )
    except Exception as error:  # noqa: BLE001
        raise _supabase_fail(error, "수정 조각을 처리하지 못했습니다") from error
    row = _first(updated)
    if not row:
        raise ReadingInviteError("수정 조각을 찾을 수 없습니다.", status="not_found")
    loaded["change"] = _change_payload(row, display_status=next_status)
    return loaded

