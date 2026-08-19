"""Lazy Supabase client for optional desktop sync.

Missing config or a failed login must not break local writing.
"""

from __future__ import annotations

import sys
from typing import Any

from env_loader import get_env, load_all_dotenv

load_all_dotenv()

_client: Any | None = None
_resolved = False


def _warn(message: str) -> None:
    print(f"경고: {message}", file=sys.stderr)


def reset_supabase_client_cache() -> None:
    """Test helper: allow get_supabase_client() to run again."""
    global _client, _resolved
    _client = None
    _resolved = False


def get_supabase_client() -> Any | None:
    """Return a logged-in Supabase client, or None if sync is not configured."""
    global _client, _resolved
    if _resolved:
        return _client

    _resolved = True
    url = get_env("SUPABASE_URL")
    anon_key = get_env("SUPABASE_ANON_KEY")
    email = get_env("SUPABASE_USER_EMAIL")
    password = get_env("SUPABASE_USER_PASSWORD")
    if not url or not anon_key or not email or not password:
        _warn(
            "Supabase 연동 설정이 없어 동기화를 건너뜁니다. "
            "(.env 에 SUPABASE_URL, SUPABASE_ANON_KEY, "
            "SUPABASE_USER_EMAIL, SUPABASE_USER_PASSWORD)"
        )
        _client = None
        return None

    try:
        from supabase import create_client
    except ImportError:
        _warn("supabase 패키지가 설치되어 있지 않아 동기화를 건너뜁니다.")
        _client = None
        return None

    try:
        client = create_client(url, anon_key)
        client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as error:  # noqa: BLE001 — optional sync must not crash the app
        _warn(f"Supabase 로그인에 실패했습니다: {error}")
        _client = None
        return None

    _client = client
    return _client
