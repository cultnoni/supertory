"""Shared helpers for reading the signed-in Supabase user from a client."""

from __future__ import annotations


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
