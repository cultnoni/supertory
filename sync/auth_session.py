"""Persist a Supabase auth session on this machine (tokens only, never a password)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

AUTH_SESSION_FILENAME = "auth_session.json"


def auth_session_path() -> Path:
    from sync.device import resolve_data_dir

    return resolve_data_dir() / AUTH_SESSION_FILENAME


def save_session(
    access_token: str,
    refresh_token: str,
    user_id: str,
    email: str,
) -> Path:
    """Write access/refresh tokens to auth_session.json. Never stores a password."""
    path = auth_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": str(access_token or ""),
        "refresh_token": str(refresh_token or ""),
        "user_id": str(user_id or ""),
        "email": str(email or ""),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_session() -> dict[str, str] | None:
    """Return the saved session dict, or None if missing/corrupt/incomplete."""
    path = auth_session_path()
    if not path.is_file():
        return None
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    access = str(raw.get("access_token") or "").strip()
    refresh = str(raw.get("refresh_token") or "").strip()
    if not access or not refresh:
        return None
    return {
        "access_token": access,
        "refresh_token": refresh,
        "user_id": str(raw.get("user_id") or "").strip(),
        "email": str(raw.get("email") or "").strip(),
    }


def clear_session() -> None:
    """Delete the session file (logout). Missing file is fine."""
    path = auth_session_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
