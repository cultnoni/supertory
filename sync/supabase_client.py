"""저장된 세션으로 Supabase 클라이언트를 복원한다. 로그인되지 않았으면 None (로컬 작성은 계속)."""

from __future__ import annotations

import sys
import threading
from typing import Any

from env_loader import get_env, load_all_dotenv
from sync.auth_session import clear_session, load_session, save_session

load_all_dotenv()

_client: Any | None = None
_resolved = False

_EMAIL_CONFIRM_MESSAGE = (
    "확인 이메일을 확인해 주세요. 메일함에서 가입을 완료한 뒤 로그인해 주세요."
)


def _warn(message: str) -> None:
    print(f"경고: {message}", file=sys.stderr)


def reset_supabase_client_cache() -> None:
    """Test helper: allow get_supabase_client() to run again."""
    global _client, _resolved
    _client = None
    _resolved = False


def _set_client(client: Any | None, *, resolved: bool = True) -> Any | None:
    global _client, _resolved
    _client = client
    _resolved = resolved
    return _client


def _attr_or_key(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _session_tokens(session: Any) -> tuple[str, str] | None:
    if session is None:
        return None
    access = str(_attr_or_key(session, "access_token") or "").strip()
    refresh = str(_attr_or_key(session, "refresh_token") or "").strip()
    if not access or not refresh:
        return None
    return access, refresh


def _user_fields(user: Any) -> dict[str, str] | None:
    if user is None:
        return None
    user_id = _attr_or_key(user, "id")
    email = _attr_or_key(user, "email")
    user_id_text = str(user_id or "").strip()
    email_text = str(email or "").strip()
    if not user_id_text and not email_text:
        return None
    return {"id": user_id_text, "email": email_text}


def _user_from_auth_payload(response: Any, session: Any = None) -> dict[str, str] | None:
    user = _attr_or_key(response, "user")
    fields = _user_fields(user)
    if fields:
        return fields
    session = session if session is not None else _attr_or_key(response, "session")
    return _user_fields(_attr_or_key(session, "user"))


def _persist_from_session(session: Any, user: Any | None = None) -> dict[str, str] | None:
    tokens = _session_tokens(session)
    if not tokens:
        return None
    access, refresh = tokens
    fields = _user_fields(user) or _user_fields(_attr_or_key(session, "user")) or {}
    save_session(
        access_token=access,
        refresh_token=refresh,
        user_id=fields.get("id") or "",
        email=fields.get("email") or "",
    )
    return {"id": fields.get("id") or "", "email": fields.get("email") or ""}


def _bind_session_persistence(client: Any) -> None:
    """Keep auth_session.json in sync when tokens rotate in this process."""

    def _on_event(event: str, session: Any) -> None:
        if event in {"TOKEN_REFRESHED", "SIGNED_IN", "USER_UPDATED"} and session is not None:
            _persist_from_session(session)
        elif event == "SIGNED_OUT":
            clear_session()

    try:
        client.auth.on_auth_state_change(_on_event)
    except Exception:  # noqa: BLE001 — persistence is optional
        pass


def _create_anon_client() -> Any | None:
    url = get_env("SUPABASE_URL")
    anon_key = get_env("SUPABASE_ANON_KEY")
    if not url or not anon_key:
        return None
    try:
        from supabase import create_client
    except ImportError:
        _warn("supabase 패키지가 설치되어 있지 않아 동기화를 건너뜁니다.")
        return None
    try:
        return create_client(url, anon_key)
    except Exception as error:  # noqa: BLE001
        _warn(f"Supabase 클라이언트 생성에 실패했습니다: {error}")
        return None


def _auth_error_message(error: BaseException) -> str:
    raw = str(getattr(error, "message", None) or error or "").strip()
    lowered = raw.lower()
    if "invalid login" in lowered or "invalid_credentials" in lowered:
        return "이메일 또는 비밀번호가 올바르지 않습니다."
    if "already registered" in lowered or "user already" in lowered:
        return "이미 가입된 이메일입니다."
    if "email not confirmed" in lowered:
        return _EMAIL_CONFIRM_MESSAGE
    return raw or "계정 요청에 실패했습니다."


def _normalize_email_password(email: str, password: str) -> tuple[str, str] | dict[str, Any]:
    email_text = str(email or "").strip()
    password_text = str(password or "")
    if not email_text or "@" not in email_text:
        return {"ok": False, "error": "이메일 주소를 입력해 주세요."}
    if not password_text:
        return {"ok": False, "error": "비밀번호를 입력해 주세요."}
    if len(password_text) < 6:
        return {"ok": False, "error": "비밀번호는 6자 이상이어야 합니다."}
    return email_text, password_text


def _should_clear_session_on_restore_error(error: BaseException) -> bool:
    """Keep the file on network blips; drop it only when the token is actually invalid."""
    text = str(getattr(error, "message", None) or error or "").lower()
    network_needles = (
        "timeout",
        "timed out",
        "getaddrinfo",
        "name or service not known",
        "failed to resolve",
        "nodename nor servname",
        "network is unreachable",
        "connection",
        "offline",
        "winerror",
        "temporarily unavailable",
        "failed to establish",
        "max retries",
        "connect error",
        "unreachable",
    )
    if any(needle in text for needle in network_needles):
        return False
    invalid_needles = (
        "invalid",
        "expired",
        "refresh_token",
        "not authenticated",
        "jwt",
        "invalid login",
        "invalid_credentials",
    )
    return any(needle in text for needle in invalid_needles)


def restore_session(*, timeout_sec: float = 5.0) -> Any | None:
    """Hydrate the client from auth_session.json. Rotates tokens; clears file on auth failure."""
    payload = load_session()
    if not payload:
        return None

    def _restore() -> Any | None:
        client = _create_anon_client()
        if client is None:
            return None
        try:
            response = client.auth.set_session(
                payload["access_token"],
                payload["refresh_token"],
            )
            session = _attr_or_key(response, "session") or response
            tokens = _session_tokens(session)
            if not tokens:
                raise RuntimeError("세션 토큰을 복원하지 못했습니다.")
            user = _user_from_auth_payload(response, session)
            _persist_from_session(session, user)
            _bind_session_persistence(client)
            return _set_client(client)
        except Exception as error:  # noqa: BLE001
            _warn(f"저장된 계정 세션을 복원하지 못했습니다: {error}")
            if _should_clear_session_on_restore_error(error):
                clear_session()
            return None

    if timeout_sec and timeout_sec > 0:
        box: list[Any] = []

        def _run() -> None:
            box.append(_restore())

        worker = threading.Thread(target=_run, daemon=True, name="supabase-restore")
        worker.start()
        worker.join(timeout=float(timeout_sec))
        if worker.is_alive():
            _warn("세션 복원 시간 초과 — 로컬으로 계속합니다.")
            return None
        return box[0] if box else None
    return _restore()


def sign_up(email: str, password: str) -> dict[str, Any]:
    """Create a user. May return needs_email_confirmation instead of a live session."""
    normalized = _normalize_email_password(email, password)
    if isinstance(normalized, dict):
        return normalized
    email_text, password_text = normalized

    client = _create_anon_client()
    if client is None:
        return {
            "ok": False,
            "error": "계정 서버에 연결할 수 없습니다.",
            "status": 503,
        }

    try:
        response = client.auth.sign_up(
            {"email": email_text, "password": password_text}
        )
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": _auth_error_message(error)}

    session = _attr_or_key(response, "session")
    user = _user_from_auth_payload(response, session)
    tokens = _session_tokens(session)
    if tokens:
        _persist_from_session(session, user)
        _bind_session_persistence(client)
        _set_client(client)
        return {
            "ok": True,
            "needs_email_confirmation": False,
            "user": user,
        }

    return {
        "ok": True,
        "needs_email_confirmation": True,
        "message": _EMAIL_CONFIRM_MESSAGE,
        "user": user,
    }


def sign_in(email: str, password: str) -> dict[str, Any]:
    """Password login, persist tokens, and replace the cached client."""
    normalized = _normalize_email_password(email, password)
    if isinstance(normalized, dict):
        return normalized
    email_text, password_text = normalized

    client = _create_anon_client()
    if client is None:
        return {
            "ok": False,
            "error": "계정 서버에 연결할 수 없습니다.",
            "status": 503,
        }

    try:
        response = client.auth.sign_in_with_password(
            {"email": email_text, "password": password_text}
        )
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": _auth_error_message(error)}

    session = _attr_or_key(response, "session")
    tokens = _session_tokens(session)
    if not tokens:
        return {
            "ok": False,
            "error": _EMAIL_CONFIRM_MESSAGE,
        }

    user = _user_from_auth_payload(response, session)
    _persist_from_session(session, user)
    _bind_session_persistence(client)
    _set_client(client)
    return {"ok": True, "user": user}


def sign_out() -> dict[str, Any]:
    """Sign out the user session and leave the app logged out."""
    client = _client
    if client is not None:
        try:
            client.auth.sign_out()
        except Exception as error:  # noqa: BLE001
            _warn(f"Supabase 로그아웃에 실패했습니다: {error}")
    clear_session()
    _set_client(None, resolved=True)
    return {"ok": True}


def get_current_user() -> dict[str, str] | None:
    """Return the signed-in user from the session file, or None if logged out."""
    if load_session() is None:
        return None
    client = _client
    if client is None:
        client = restore_session()
    if client is None:
        return None
    try:
        result = client.auth.get_user()
        user = _attr_or_key(result, "user")
        if user is None and isinstance(result, dict):
            user = result.get("user")
        return _user_fields(user)
    except Exception:  # noqa: BLE001
        return None


def get_supabase_client() -> Any | None:
    """Return a hydrated client. Never blocks on a live network restore.

    Restore runs in the background at startup and when get_current_user() is
    called (admin / reading invites). Local manuscript paths must not wait.
    """
    if _client is not None:
        return _client
    if load_session() is None:
        return _set_client(None, resolved=True)
    return None
