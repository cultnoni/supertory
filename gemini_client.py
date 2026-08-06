"""Google Gemini API helper for SuperTory (stdlib urllib only)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from env_loader import apply_bundled_defaults, get_env, load_all_dotenv, load_dotenv

# Prefer .env (source / next to frozen exe / MEIPASS), then build-time defaults.
load_all_dotenv()

DEFAULT_MODEL = "gemini-flash-lite-latest"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiError(RuntimeError):
    """Raised when the Gemini API cannot complete a request."""


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
            "Gemini API 키가 없습니다. 프로젝트 폴더의 .env 파일에 GEMINI_API_KEY를 넣어 주세요."
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
        with urllib.request.urlopen(request, timeout=90) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        message = _extract_api_error(detail) or f"HTTP {error.code}"
        raise GeminiError(f"Gemini 호출 실패: {message}") from error
    except urllib.error.URLError as error:
        raise GeminiError(f"Gemini에 연결하지 못했습니다: {error.reason}") from error

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GeminiError("Gemini 응답을 해석하지 못했습니다.") from error

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
        raise GeminiError("Gemini가 빈 응답을 돌려주었습니다. 잠시 후 다시 시도해 주세요.")
    return text


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
