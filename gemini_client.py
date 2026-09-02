"""Google Gemini API helper for SuperTory (stdlib urllib only)."""

from __future__ import annotations

import errno
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from typing import Any

from env_loader import apply_bundled_defaults, get_env, load_all_dotenv, load_dotenv

# Prefer .env (source / next to frozen exe / MEIPASS), then build-time defaults.
load_all_dotenv()

DEFAULT_MODEL = "gemini-flash-lite-latest"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

GEMINI_ERROR_CODES = (
    "quota",
    "rate_limit",
    "auth",
    "empty",
    "network",
    "timeout",
    "unknown",
)
NETWORK_USER_MESSAGE = "인터넷 연결이 필요해요. 연결을 확인한 뒤 다시 시도해 주세요."
API_USER_MESSAGE = "AI 응답을 받지 못했어요. 잠시 후 다시 시도해 주세요."
DEFAULT_TIMEOUT_SECONDS = 20.0

_CONNECTIVITY_ERRNOS = {
    value
    for value in (
        getattr(errno, "ENETUNREACH", None),
        getattr(errno, "EHOSTUNREACH", None),
        getattr(errno, "ENETDOWN", None),
        getattr(errno, "EHOSTDOWN", None),
        getattr(errno, "EAI_AGAIN", None),
        getattr(errno, "EAI_NONAME", None),
        getattr(errno, "EAI_NODATA", None),
        getattr(errno, "EAI_FAIL", None),
    )
    if value is not None
}
_CONNECTIVITY_WINERRORS = {10050, 10051, 10065, 11001, 11002, 11003, 11004}
_TIMEOUT_ERRNOS = {
    value
    for value in (getattr(errno, "ETIMEDOUT", None),)
    if value is not None
}
_TIMEOUT_WINERRORS = {10060}
_CONNECTIVITY_MARKERS = (
    "network is unreachable",
    "no route to host",
    "getaddrinfo failed",
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
    "enotfound",
    "eai_again",
    "eai_noname",
    "offline",
)
_TIMEOUT_MARKERS = (
    "timed out",
    "the read operation timed out",
    "timeout",
)


class GeminiError(RuntimeError):
    """Raised when the Gemini API cannot complete a request."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "unknown",
        http_status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        normalized = str(code or "unknown").strip() or "unknown"
        self.code = normalized if normalized in GEMINI_ERROR_CODES else "unknown"
        self.http_status = int(http_status) if http_status is not None else None
        self.retry_after = float(retry_after) if retry_after is not None else None


def is_configured() -> bool:
    key = get_env("GEMINI_API_KEY")
    return bool(key) and key not in {"your_gemini_api_key_here", "changeme"}


def model_name() -> str:
    return get_env("GEMINI_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def generate_text(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.8,
    max_output_tokens: int = 2048,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Call Gemini generateContent and return plain text."""
    if not is_configured():
        raise GeminiError(
            "Gemini API 키가 없습니다. 프로젝트 폴더의 .env 파일에 GEMINI_API_KEY를 넣어 주세요.",
            code="auth",
        )
    api_key = get_env("GEMINI_API_KEY")
    assert api_key is not None

    model = model_name()
    url = f"{API_BASE}/{model}:generateContent?key={api_key}"

    user_text = prompt.strip()
    if system:
        # Some models accept systemInstruction; keep a portable fallback in the prompt.
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {
                "temperature": max(0.0, min(2.0, float(temperature))),
                "maxOutputTokens": max(64, min(8192, int(max_output_tokens))),
            },
        }
    else:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {
                "temperature": max(0.0, min(2.0, float(temperature))),
                "maxOutputTokens": max(64, min(8192, int(max_output_tokens))),
            },
        }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    wait = max(5.0, float(timeout or DEFAULT_TIMEOUT_SECONDS))
    try:
        with urllib.request.urlopen(request, timeout=wait) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        header_retry = None
        try:
            header_retry = error.headers.get("Retry-After") if error.headers else None
        except Exception:
            header_retry = None
        code, retry_after, message = classify_gemini_http_error(
            error.code, detail, retry_after_header=header_retry
        )
        _log_gemini_failure(code, error, detail=message, http_status=int(error.code))
        raise GeminiError(
            _user_message_for_code(code),
            code=code,
            http_status=int(error.code),
            retry_after=retry_after,
        ) from error
    except (TimeoutError, urllib.error.URLError, OSError) as error:
        code = classify_transport_error(error)
        _log_gemini_failure(code, error)
        raise GeminiError(
            _user_message_for_code(code),
            code=code,
        ) from error

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as error:
        _log_gemini_failure("unknown", error, detail="json")
        raise GeminiError(
            API_USER_MESSAGE,
            code="unknown",
        ) from error

    text = _extract_text(body)
    if not text:
        # Retry without systemInstruction for older model quirks.
        if system:
            return generate_text(
                f"{system}\n\n---\n\n{prompt}",
                system=None,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                timeout=wait,
            )
        raise GeminiError(
            API_USER_MESSAGE,
            code="empty",
        )
    return text


def classify_transport_error(error: BaseException) -> str:
    """Return network | timeout | unknown for urllib/socket failures."""
    saw_network = False
    saw_timeout = False
    for item in _iter_error_chain(error):
        if isinstance(item, TimeoutError):
            saw_timeout = True
        if isinstance(item, socket.gaierror):
            saw_network = True
        err_no = getattr(item, "errno", None)
        winerror = getattr(item, "winerror", None)
        if err_no in _CONNECTIVITY_ERRNOS or winerror in _CONNECTIVITY_WINERRORS:
            saw_network = True
        if err_no in _TIMEOUT_ERRNOS or winerror in _TIMEOUT_WINERRORS:
            saw_timeout = True
        text = str(item or "").lower()
        reason = getattr(item, "reason", None)
        if reason is not None and not isinstance(reason, BaseException):
            text = f"{text} {reason}".lower()
        if any(marker in text for marker in _CONNECTIVITY_MARKERS):
            saw_network = True
        if any(marker in text for marker in _TIMEOUT_MARKERS):
            saw_timeout = True
    if saw_network:
        return "network"
    if saw_timeout:
        return "timeout"
    return "unknown"


def classify_gemini_http_error(
    http_status: int,
    detail: str,
    *,
    retry_after_header: str | None = None,
) -> tuple[str, float | None, str]:
    """Return (code, retry_after_seconds, message) for an HTTP error body."""
    status = int(http_status)
    payload = _parse_error_payload(detail)
    message = _extract_api_error(detail) or f"HTTP {status}"
    retry_after = _retry_after_seconds(payload, message, retry_after_header)
    if status in {401, 403}:
        return "auth", retry_after, message
    if status == 429:
        quota_ids = _collect_quota_ids(payload)
        if any("perday" in token.lower() for token in quota_ids):
            return "quota", retry_after, message
        return "rate_limit", retry_after, message
    return "unknown", retry_after, message


def _parse_error_payload(detail: str) -> dict[str, Any]:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _collect_quota_ids(payload: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    error = payload.get("error")
    root = error if isinstance(error, dict) else payload
    details = root.get("details") if isinstance(root, dict) else None
    if not isinstance(details, list):
        return tokens
    keys = ("quotaId", "quota_id", "quotaMetric", "quota_metric", "quota_limit")
    for item in details:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if value:
                tokens.append(str(value))
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            for key in keys:
                value = metadata.get(key)
                if value:
                    tokens.append(str(value))
        violations = item.get("violations")
        if isinstance(violations, list):
            for violation in violations:
                if not isinstance(violation, dict):
                    continue
                for key in keys:
                    value = violation.get(key)
                    if value:
                        tokens.append(str(value))
    return tokens


def _retry_after_seconds(
    payload: dict[str, Any],
    message: str,
    header_value: str | None,
) -> float | None:
    error = payload.get("error")
    root = error if isinstance(error, dict) else payload
    details = root.get("details") if isinstance(root, dict) else None
    if isinstance(details, list):
        for item in details:
            if not isinstance(item, dict):
                continue
            raw = item.get("retryDelay") or item.get("retry_delay")
            parsed = _parse_duration_seconds(raw)
            if parsed is not None:
                return parsed
    header_parsed = _parse_duration_seconds(header_value)
    if header_parsed is not None:
        return header_parsed
    match = re.search(r"retry in\s+([\d.]+)\s*s", str(message or ""), re.I)
    if match:
        return _parse_duration_seconds(match.group(1))
    return None


def _parse_duration_seconds(raw: object) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.lower().endswith("s") and not text.lower().endswith("ms"):
        text = text[:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def _extract_text(body: dict[str, Any]) -> str:
    candidates = body.get("candidates") or []
    chunks: list[str] = []
    for candidate in candidates:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            piece = part.get("text")
            if piece:
                chunks.append(str(piece))
    return "\n".join(chunks).strip()


def _extract_api_error(detail: str) -> str:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return detail[:300]
    error = payload.get("error") or {}
    message = error.get("message")
    return str(message) if message else detail[:300]


def _iter_error_chain(error: BaseException):
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException):
            current = reason
            continue
        current = current.__cause__ or current.__context__


def _user_message_for_code(code: str) -> str:
    if code == "network":
        return NETWORK_USER_MESSAGE
    if code == "auth":
        return (
            "Gemini API 키가 없거나 권한이 없습니다. "
            "프로젝트 폴더의 .env 파일에 GEMINI_API_KEY를 확인해 주세요."
        )
    return API_USER_MESSAGE


def _log_gemini_failure(
    code: str,
    error: BaseException,
    *,
    detail: str = "",
    http_status: int | None = None,
) -> None:
    extra = f" status={http_status}" if http_status is not None else ""
    snippet = str(detail or error).replace("\n", " ").strip()[:300]
    print(
        f"[gemini] code={code}{extra} type={type(error).__name__}: {snippet}",
        file=sys.stderr,
        flush=True,
    )


def user_visible_message(error: BaseException) -> str:
    """Short copy for toasts. True offline stays distinct from API failures."""
    if isinstance(error, GeminiError):
        if error.code == "network":
            return NETWORK_USER_MESSAGE
        if error.code == "auth":
            text = str(error).strip()
            return text or _user_message_for_code("auth")
        return API_USER_MESSAGE
    text = str(error or "").strip()
    return text or API_USER_MESSAGE


def status() -> dict[str, Any]:
    return {
        "configured": is_configured(),
        "model": model_name() if is_configured() else None,
        "provider": "google-gemini",
    }


# Reload env when module imported after app start in tests.
def reload_env() -> None:
    load_dotenv(override=True)
    # Clear accidental blank overrides from process env during tests.
    if os.environ.get("GEMINI_API_KEY", "").strip() == "":
        os.environ.pop("GEMINI_API_KEY", None)
    apply_bundled_defaults()
