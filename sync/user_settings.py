"""Load and save account user_settings (primary device + conflict consent)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.conflict_resolution_service import DEVICE_TYPES, normalize_device_type
from sync.supabase_client import get_current_user, get_supabase_client

SETTINGS_COLUMNS = (
    "user_id, primary_device_type, conflict_policy_agreed_at, created_at, updated_at"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_settings(row: object) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    user_id = str(row.get("user_id") or "").strip()
    primary = str(row.get("primary_device_type") or "").strip()
    if not user_id or primary not in DEVICE_TYPES:
        return None
    agreed = row.get("conflict_policy_agreed_at")
    agreed_text = str(agreed).strip() if agreed is not None else ""
    return {
        "user_id": user_id,
        "primary_device_type": primary,
        "conflict_policy_agreed_at": agreed_text or None,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def fetch_user_settings(user_id: str) -> dict[str, Any] | None:
    client = get_supabase_client()
    if client is None:
        return None
    owner = str(user_id or "").strip()
    if not owner:
        return None
    found = (
        client.table("user_settings")
        .select(SETTINGS_COLUMNS)
        .eq("user_id", owner)
        .limit(1)
        .execute()
    )
    rows = getattr(found, "data", None) or []
    if not rows:
        return None
    return _row_to_settings(rows[0])


def save_user_settings(
    user_id: str,
    primary_device_type: str,
    *,
    agree: bool = False,
) -> dict[str, Any]:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("sync_not_configured")
    owner = str(user_id or "").strip()
    if not owner:
        raise ValueError("로그인된 사용자가 없습니다.")
    primary = normalize_device_type(primary_device_type)
    existing = fetch_user_settings(owner)
    agreed_at = (existing or {}).get("conflict_policy_agreed_at")
    if not agreed_at:
        if not agree:
            raise ValueError("충돌 정책에 동의해 주세요.")
        agreed_at = _now_iso()
    now = _now_iso()
    row = {
        "user_id": owner,
        "primary_device_type": primary,
        "conflict_policy_agreed_at": agreed_at,
        "updated_at": now,
    }
    if existing is None:
        row["created_at"] = now
    try:
        saved = client.table("user_settings").upsert(row, on_conflict="user_id").execute()
    except Exception as error:  # noqa: BLE001
        raise RuntimeError("우선 기기 설정을 저장하지 못했습니다.") from error
    rows = getattr(saved, "data", None) or []
    parsed = _row_to_settings(rows[0]) if rows else None
    return parsed or {
        "user_id": owner,
        "primary_device_type": primary,
        "conflict_policy_agreed_at": agreed_at,
        "created_at": row.get("created_at") or (existing or {}).get("created_at"),
        "updated_at": now,
    }


def current_user_settings_payload(device_type: str | None = None) -> dict[str, Any]:
    """Payload for GET /api/user-settings. Includes HTTP `status` when blocked."""
    current = None
    if device_type:
        try:
            current = normalize_device_type(device_type)
        except ValueError:
            current = None
    user = get_current_user()
    if not user or not str(user.get("id") or "").strip():
        return {
            "settings": None,
            "needs_consent": False,
            "current_device_type": current,
            "user": None,
        }
    if get_supabase_client() is None:
        return {
            "error": "sync_not_configured",
            "status": 503,
            "settings": None,
            "needs_consent": False,
            "current_device_type": current,
            "user": user,
        }
    try:
        settings = fetch_user_settings(str(user["id"]))
    except Exception:  # noqa: BLE001 — table missing or network
        return {
            "error": "sync_not_configured",
            "status": 503,
            "settings": None,
            "needs_consent": False,
            "current_device_type": current,
            "user": user,
        }
    agreed = bool(settings and settings.get("conflict_policy_agreed_at"))
    return {
        "settings": settings,
        "needs_consent": not agreed,
        "current_device_type": current,
        "user": {"id": user.get("id"), "email": user.get("email")},
    }
