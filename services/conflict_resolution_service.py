"""Primary-device-wins write resolver for multi-device document mirrors."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

DEVICE_TYPES = ("desktop", "browser", "mobile")
HELD_MESSAGE = "우선 기기에서 최근 변경사항이 있어 저장이 보류되었습니다"


class ConflictStore(Protocol):
    def get_user_settings(self, user_id: str) -> dict[str, Any] | None:
        """Return {primary_device_type, ...} or None."""

    def get_record(self, source_table: str, record_id: str) -> dict[str, Any] | None:
        """Return {updated_at, content, device_type?} or None if missing."""

    def save_record(
        self,
        source_table: str,
        record_id: str,
        content: Any,
        updated_at: str,
        device_type: str,
    ) -> None:
        """Persist the winning content."""

    def insert_conflict_backup(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Store a losing snapshot. Must return the inserted row including id."""


class MissingUserSettings(ValueError):
    """Raised when resolve_write runs before primary-device onboarding."""


def normalize_device_type(value: object) -> str:
    text = str(value or "").strip().lower()
    if text not in DEVICE_TYPES:
        raise ValueError("우선 기기를 선택해 주세요.")
    return text


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _timestamps_match(left: object, right: object) -> bool:
    parsed_left = _parse_timestamp(left)
    parsed_right = _parse_timestamp(right)
    if parsed_left is None or parsed_right is None:
        return str(left or "").strip() == str(right or "").strip()
    return parsed_left == parsed_right


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _losing_device_type(
    existing_device_type: object,
    writer_device_type: str,
    primary_device_type: str,
) -> str:
    try:
        return normalize_device_type(existing_device_type)
    except ValueError:
        pass
    if writer_device_type == primary_device_type:
        for candidate in DEVICE_TYPES:
            if candidate != writer_device_type:
                return candidate
    return primary_device_type


def _backup_losing_record(
    store: ConflictStore,
    *,
    user_id: str,
    source_table: str,
    record_id: str,
    existing: dict[str, Any],
    writer_device_type: str,
    primary_device_type: str,
    created_at: str,
) -> dict[str, Any]:
    losing_type = _losing_device_type(
        existing.get("device_type"),
        writer_device_type,
        primary_device_type,
    )
    payload = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "source_table": source_table,
        "source_record_id": str(record_id),
        "losing_device_type": losing_type,
        "content_snapshot": existing.get("content"),
        "created_at": created_at,
    }
    saved = store.insert_conflict_backup(payload)
    return saved if isinstance(saved, dict) else payload


def resolve_write(
    user_id: str,
    source_table: str,
    record_id: str,
    device_type: str,
    new_content: Any,
    last_known_updated_at: str | None,
    store: ConflictStore,
    *,
    force: bool = False,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Apply a write using the account's primary-device conflict policy.

    Returns a status dict. Does not talk to live manuscript APIs.
    """
    owner_id = str(user_id or "").strip()
    if not owner_id:
        raise ValueError("로그인된 사용자가 없습니다.")
    table = str(source_table or "").strip()
    if not table:
        raise ValueError("원본 테이블이 없습니다.")
    row_id = str(record_id or "").strip()
    if not row_id:
        raise ValueError("원본 레코드 ID가 없습니다.")
    writer = normalize_device_type(device_type)

    settings = store.get_user_settings(owner_id)
    if not settings:
        raise MissingUserSettings("우선 기기를 먼저 선택해 주세요.")
    primary = normalize_device_type(settings.get("primary_device_type"))

    existing = store.get_record(table, row_id)
    conflict = False
    if existing is not None:
        server_updated = existing.get("updated_at")
        conflict = not _timestamps_match(server_updated, last_known_updated_at)

    if conflict and writer != primary and not force:
        return {
            "status": "held",
            "conflict": True,
            "saved": False,
            "message": HELD_MESSAGE,
            "primary_device_type": primary,
            "device_type": writer,
        }

    clock = now or _utc_now
    stamp = clock().isoformat()
    backup = None
    if conflict and existing is not None:
        backup = _backup_losing_record(
            store,
            user_id=owner_id,
            source_table=table,
            record_id=row_id,
            existing=existing,
            writer_device_type=writer,
            primary_device_type=primary,
            created_at=stamp,
        )

    store.save_record(table, row_id, new_content, stamp, writer)
    result: dict[str, Any] = {
        "status": "saved",
        "conflict": conflict,
        "saved": True,
        "updated_at": stamp,
        "primary_device_type": primary,
        "device_type": writer,
        "forced": bool(force and conflict and writer != primary),
    }
    if backup is not None:
        result["backup_id"] = backup.get("id")
        result["backup"] = backup
    return result
