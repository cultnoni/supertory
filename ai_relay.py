"""AI 중계 창구 클라이언트 (docs/ai-relay-design.md).

relay 모드에서는 Gemini·Claude를 직접 부르지 않고 Supabase 서버 함수 `ai`를 거친다.
direct 모드(개발용)는 기존처럼 .env 키로 직접 부른다.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterator

from env_loader import get_env

# 창구 오류 코드 → 사용자 문구. 코드는 gemini_client.GEMINI_ERROR_CODES에도 들어 있다.
RELAY_ERROR_MESSAGES = {
    "login_required": "로그인하면 AI를 쓸 수 있어요.",
    "feature_disabled": "이 기능은 잠시 쉬고 있어요. 잠시 후 다시 시도해 주세요.",
    "no_access": "AI 기능은 베타 이용권이 있어야 쓸 수 있어요.",
    "daily_limit": "오늘 쓸 수 있는 AI 사용량을 다 썼어요. 내일 다시 쓸 수 있어요.",
    "service_limit": "지금은 AI 사용이 몰렸어요. 잠시 후 다시 시도해 주세요.",
    "bad_request": "요청을 처리하지 못했어요.",
}
RELAY_CODES = tuple(RELAY_ERROR_MESSAGES)

LOGICAL_MODELS = {
    "gemini-3.5-flash-lite": "gemini:default",
    "claude-sonnet-5": "claude:feedback",
    "claude-haiku-5-5": "claude:light",
}


class RelayError(RuntimeError):
    """창구가 요청을 거절했거나 제공사 오류를 돌려줬을 때."""

    def __init__(self, code: str, *, http_status: int | None = None,
                 provider_status: int | None = None, detail: str = "",
                 retry_after: float | None = None) -> None:
        super().__init__(RELAY_ERROR_MESSAGES.get(code, code))
        self.code = code
        self.http_status = http_status
        self.provider_status = provider_status
        self.detail = detail
        self.retry_after = retry_after


def mode() -> str:
    """'relay' 또는 'direct'. 환경 변수가 우선, 없으면 AI 키가 있을 때만 direct."""
    forced = str(get_env("SUPERTORY_AI_MODE") or "").strip().lower()
    if forced in {"relay", "direct"}:
        return forced
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        value = str(get_env(name) or "").strip()
        if value and value not in {"your_gemini_api_key_here", "changeme"}:
            return "direct"
    return "relay"


def is_relay() -> bool:
    return mode() == "relay"


def _base_url() -> str:
    url = str(get_env("SUPABASE_URL") or "").strip().rstrip("/")
    return f"{url}/functions/v1/ai" if url else ""


def _access_token() -> str | None:
    try:
        from sync.supabase_client import get_supabase_client

        client = get_supabase_client()
        if client is not None:
            session = client.auth.get_session()
            token = getattr(session, "access_token", None)
            if token:
                return str(token)
    except Exception:  # noqa: BLE001 — 파일 세션으로 대체
        pass
    from sync.auth_session import load_session

    saved = load_session()
    return str(saved.get("access_token") or "") or None if saved else None


def _open(path: str, payload: dict[str, Any] | None, timeout: float):
    base = _base_url()
    if not base:
        raise RelayError("login_required")
    token = _access_token()
    if not token:
        raise RelayError("login_required")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    anon = str(get_env("SUPABASE_ANON_KEY") or "").strip()
    if anon:
        headers["apikey"] = anon
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base}/{path}", data=data, headers=headers, method="POST" if payload is not None else "GET"
    )
    try:
        return urllib.request.urlopen(request, timeout=max(5.0, float(timeout)))  # noqa: S310
    except urllib.error.HTTPError as error:
        raise _error_from_http(error) from None


def _error_from_http(error: urllib.error.HTTPError) -> RelayError:
    raw = error.read().decode("utf-8", errors="replace")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = {}
    code = str(body.get("error") or "") if isinstance(body, dict) else ""
    retry_after = None
    try:
        header = error.headers.get("retry-after") if error.headers else None
        retry_after = float(header) if header else None
    except (TypeError, ValueError):
        retry_after = None
    provider_status = body.get("provider_status") if isinstance(body, dict) else None
    detail = str(body.get("detail") or "") if isinstance(body, dict) else raw[:500]
    if code not in RELAY_CODES and code != "provider_error":
        code = "login_required" if error.code == 401 else "provider_error"
    return RelayError(code, http_status=int(error.code),
                      provider_status=int(provider_status) if provider_status else None,
                      detail=detail, retry_after=retry_after)


def logical_model(actual: str) -> str:
    return LOGICAL_MODELS.get(str(actual or "").strip(), "claude:feedback")


def call_gemini(feature: str, request: dict[str, Any], *, timeout: float,
                app_version: str = "") -> dict[str, Any]:
    payload = {"feature": feature or "unknown", "model": "gemini:default",
               "app_version": app_version, "request": request}
    with _open("gemini", payload, timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _sse_events(lines: Iterator[bytes]) -> Iterator[dict[str, Any]]:
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:])
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def call_claude(feature: str, model: str, request: dict[str, Any], *, timeout: float,
                app_version: str = "") -> tuple[str, dict[str, Any]]:
    """창구의 스트리밍 응답을 모아 (text, body)로 돌려준다. body는 Messages 응답 모양."""
    payload = {"feature": feature or "feedback.unknown", "model": logical_model(model),
               "app_version": app_version, "request": request}
    texts: list[str] = []
    usage: dict[str, int] = {}
    stop_reason = None
    with _open("claude", payload, timeout) as response:
        for event in _sse_events(iter(response)):
            kind = event.get("type")
            if kind == "message_start":
                usage.update({k: int(v or 0) for k, v in (event.get("message", {}).get("usage") or {}).items()
                              if isinstance(v, (int, float))})
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    texts.append(str(delta.get("text") or ""))
            elif kind == "message_delta":
                stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
                usage.update({k: int(v or 0) for k, v in (event.get("usage") or {}).items()
                              if isinstance(v, (int, float))})
            elif kind == "error":
                err = event.get("error") or {}
                raise RelayError("provider_error", detail=str(err.get("message") or "")[:500])
    body = {"stop_reason": stop_reason, "usage": usage}
    return "".join(texts), body


def get_quota(*, timeout: float = 10.0) -> dict[str, Any]:
    with _open("quota", None, timeout) as response:
        return json.loads(response.read().decode("utf-8"))
