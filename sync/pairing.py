"""모바일 페어링용 6자리 코드를 Supabase pairing_codes에 잠시 저장한다."""

from __future__ import annotations

import secrets
import sys
from datetime import datetime, timedelta, timezone

from sync.supabase_client import get_supabase_client

CODE_DIGITS = 6
CODE_TTL = timedelta(minutes=10)
MAX_CODE_ATTEMPTS = 24


def _warn(message: str) -> None:
    print(f"경고: {message}", file=sys.stderr)


def _random_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def generate_pairing_code(desktop_device_id: str) -> dict[str, str] | None:
    """Insert a unique 6-digit code for this desktop. Returns None if sync is off."""
    client = get_supabase_client()
    if client is None:
        return None

    device_id = str(desktop_device_id or "").strip()
    if not device_id:
        _warn("페어링 코드를 만들 기기 ID가 없습니다.")
        return None

    expires_at = (datetime.now(timezone.utc) + CODE_TTL).isoformat()
    last_error: Exception | None = None
    for _ in range(MAX_CODE_ATTEMPTS):
        code = _random_code()
        try:
            existing = (
                client.table("pairing_codes")
                .select("code")
                .eq("code", code)
                .limit(1)
                .execute()
            )
            if getattr(existing, "data", None):
                continue
            client.table("pairing_codes").insert(
                {
                    "code": code,
                    "desktop_device_id": device_id,
                    "expires_at": expires_at,
                    "used": False,
                }
            ).execute()
            return {"code": code, "expires_at": expires_at}
        except Exception as error:  # noqa: BLE001 — unique clash or network
            last_error = error
            continue

    _warn(f"페어링 코드를 만들지 못했습니다: {last_error}")
    return None
