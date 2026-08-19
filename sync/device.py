"""Stable local device_id and optional Supabase devices-row registration."""

from __future__ import annotations

import json
import os
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sync.supabase_client import get_supabase_client

DEVICE_ID_FILENAME = "device_id.json"


def _warn(message: str) -> None:
    print(f"경고: {message}", file=sys.stderr)


def resolve_data_dir() -> Path:
    """Same rule as app.py: SUPERTORY_DATA_DIR, else frozen exe/data, else repo data/."""
    app_mod = sys.modules.get("app")
    if app_mod is not None:
        data_dir = getattr(app_mod, "DATA_DIR", None)
        if data_dir:
            return Path(data_dir)

    env = (
        os.environ.get("SUPERTORY_DATA_DIR") or os.environ.get("STORYGUIDE_DATA_DIR") or ""
    ).strip()
    if env:
        return Path(env).expanduser()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parent.parent / "data"


def _device_id_path() -> Path:
    return resolve_data_dir() / DEVICE_ID_FILENAME


def _device_name() -> str:
    try:
        name = (socket.gethostname() or "").strip()
    except OSError:
        name = ""
    return name or "SuperTory Desktop"


def get_or_create_device_id() -> str:
    """Load this computer's UUID, creating device_id.json on first run."""
    path = _device_id_path()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            existing = str(payload.get("device_id") or "").strip()
            if existing:
                uuid.UUID(existing)
                return existing
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            pass

    device_id = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"device_id": device_id}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return device_id


def get_desktop_device_id() -> str:
    return get_or_create_device_id()


def _auth_user_id(client: object) -> str | None:
    try:
        session_user = getattr(getattr(client, "auth", None), "get_user", None)
        if callable(session_user):
            result = session_user()
            user = getattr(result, "user", None) or (result.get("user") if isinstance(result, dict) else None)
            user_id = getattr(user, "id", None) if user is not None else None
            if user_id:
                return str(user_id)
    except Exception:  # noqa: BLE001
        return None
    return None


def ensure_device_registered() -> None:
    """Insert or touch this desktop in Supabase devices. No-op without a client."""
    client = get_supabase_client()
    if client is None:
        return

    try:
        device_id = get_or_create_device_id()
        now = datetime.now(timezone.utc).isoformat()
        found = (
            client.table("devices")
            .select("device_id")
            .eq("device_id", device_id)
            .limit(1)
            .execute()
        )
        rows = getattr(found, "data", None) or []
        if rows:
            (
                client.table("devices")
                .update({"last_seen_at": now})
                .eq("device_id", device_id)
                .execute()
            )
            return

        row = {
            "device_id": device_id,
            "device_type": "desktop",
            "device_name": _device_name(),
            "last_seen_at": now,
        }
        user_id = _auth_user_id(client)
        if user_id:
            row["user_id"] = user_id
        client.table("devices").insert(row).execute()
    except Exception as error:  # noqa: BLE001 — local app must keep running
        _warn(f"기기 등록에 실패했습니다: {error}")
