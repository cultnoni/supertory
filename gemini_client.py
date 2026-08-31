"""Google Gemini API helper for SuperTory (stdlib urllib only)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from env_loader import apply_bundled_defaults, get_env, load_all_dotenv, load_dotenv

# Prefer .env (source / next to frozen exe / MEIPASS), then build-time defaults.
load_all_dotenv()

DEFAULT_MODEL = "gemini-flash-lite-latest"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

GEMINI_ERROR_CODES = ("quota", "rate_limit", "auth", "empty", "network", "unknown")
NETWORK_USER_MESSAGE = "인터넷 연결이 필요해요. 연결을 확인한 뒤 다시 시도해 주세요."


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
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except TimeoutError as error:
        raise GeminiError(
            NETWORK_USER_MESSAGE,
            code="network",
        ) from error
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
        raise GeminiError(
            f"Gemini 호출 실패: {message}",
            code=code,
            http_status=int(error.code),
            retry_after=retry_after,
        ) from error
    except urllib.error.URLError as error:
        raise GeminiError(
            NETWORK_USER_MESSAGE,
            code="network",
        ) from error
    except OSError as error:
        raise GeminiError(
            NETWORK_USER_MESSAGE,
            code="network",
        ) from error

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GeminiError(
            "Gemini 응답을 해석하지 못했습니다.",
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
            )
        raise GeminiError(
            "Gemini가 빈 응답을 돌려주었습니다. 잠시 후 다시 시도해 주세요.",
            code="empty",
        )
    return text


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


def user_visible_message(error: BaseException) -> str:
    """Short copy for toasts. Network failures stay user-facing, not a traceback."""
    if isinstance(error, GeminiError) and error.code == "network":
        return NETWORK_USER_MESSAGE
    text = str(error or "").strip()
    return text or NETWORK_USER_MESSAGE


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
