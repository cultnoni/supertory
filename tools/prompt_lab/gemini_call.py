"""Thin Gemini caller for the prompt lab.

Does not modify gemini_client.py. Reuses API key loading and error classification,
and adds model override, JSON MIME, 30s timeout, 429 retries, and response metadata.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

import gemini_client
from env_loader import get_env
from gemini_client import GeminiError

JSON_MIME = "application/json"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES_429 = 3


def parse_model_json(raw: str) -> tuple[Any | None, str]:
    """Return (parsed, raw_text). Strips fenced code blocks on failure."""
    text = (raw or "").strip()
    if not text:
        return None, text
    parsed = _try_load(text)
    if parsed is not None:
        return parsed, text
    stripped = _strip_fence(text)
    if stripped != text:
        parsed = _try_load(stripped)
        if parsed is not None:
            return parsed, text
    extracted = _extract_json_object(stripped)
    if extracted:
        parsed = _try_load(extracted)
        if parsed is not None:
            return parsed, text
    return None, text


def _try_load(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return ""


def response_meta(body: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {"finish_reason": None, "usage_metadata": None}
    candidates = body.get("candidates") or []
    first = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    finish = first.get("finishReason") or first.get("finish_reason")
    usage = body.get("usageMetadata") or body.get("usage_metadata")
    return {
        "finish_reason": finish,
        "usage_metadata": usage if isinstance(usage, dict) else usage,
        "candidate_count": len(candidates) if isinstance(candidates, list) else 0,
        "prompt_feedback": body.get("promptFeedback") or body.get("prompt_feedback"),
    }


def generate(
    prompt: str,
    *,
    model: str,
    system: str | None = None,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    timeout: float = DEFAULT_TIMEOUT,
    json_output: bool = True,
    thinking_budget: int | None = None,
) -> dict[str, Any]:
    """Call generateContent. Empty text still returns finish_reason / usage_metadata."""
    last_error: GeminiError | None = None
    json_mime_used = bool(json_output)
    thinking = thinking_budget
    for attempt in range(MAX_RETRIES_429 + 1):
        try:
            text, body = _generate_once(
                prompt,
                model=model,
                system=system,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                timeout=timeout,
                json_output=json_mime_used,
                thinking_budget=thinking,
            )
            parsed, raw = parse_model_json(text)
            meta = response_meta(body)
            return {
                "text": text,
                "parsed": parsed,
                "raw": raw if not parsed else (raw if not text else None),
                "json_mime_used": json_mime_used,
                "model": model,
                "empty": not bool((text or "").strip()),
                "thinking_budget": thinking,
                **meta,
            }
        except GeminiError as error:
            last_error = error
            if error.http_status == 400 and json_mime_used:
                json_mime_used = False
                continue
            if error.http_status == 400 and thinking is not None:
                thinking = None
                continue
            retryable = error.http_status == 429 or error.code in {"rate_limit", "quota"}
            if retryable and attempt < MAX_RETRIES_429:
                wait = error.retry_after if error.retry_after and error.retry_after > 0 else 5.0 * (attempt + 1)
                time.sleep(min(60.0, float(wait)))
                continue
            raise
    assert last_error is not None
    raise last_error


def _generate_once(
    prompt: str,
    *,
    model: str,
    system: str | None,
    temperature: float,
    max_output_tokens: int,
    timeout: float,
    json_output: bool,
    thinking_budget: int | None,
) -> tuple[str, dict[str, Any]]:
    if not gemini_client.is_configured():
        raise GeminiError(
            "Gemini API 키가 없습니다. 프로젝트 폴더의 .env 파일에 GEMINI_API_KEY를 넣어 주세요.",
            code="auth",
        )
    api_key = get_env("GEMINI_API_KEY")
    assert api_key is not None
    chosen = str(model or "").strip()
    if not chosen:
        raise GeminiError("모델 이름이 비어 있습니다.", code="unknown")

    url = f"{gemini_client.API_BASE}/{chosen}:generateContent?key={api_key}"
    user_text = prompt.strip()
    generation: dict[str, Any] = {
        "temperature": max(0.0, min(2.0, float(temperature))),
        "maxOutputTokens": max(64, min(8192, int(max_output_tokens))),
    }
    if json_output:
        generation["responseMimeType"] = JSON_MIME
    if thinking_budget is not None:
        generation["thinkingConfig"] = {"thinkingBudget": int(thinking_budget)}
    payload: dict[str, Any]
    if system:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": generation,
        }
    else:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": generation,
        }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    wait = max(5.0, float(timeout or DEFAULT_TIMEOUT))
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
        code, retry_after, message = gemini_client.classify_gemini_http_error(
            error.code, detail, retry_after_header=header_retry
        )
        raise GeminiError(
            gemini_client._user_message_for_code(code),
            code=code,
            http_status=int(error.code),
            retry_after=retry_after,
        ) from error
    except (TimeoutError, urllib.error.URLError, OSError) as error:
        code = gemini_client.classify_transport_error(error)
        raise GeminiError(
            gemini_client._user_message_for_code(code),
            code=code,
        ) from error

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GeminiError(gemini_client.API_USER_MESSAGE, code="unknown") from error

    api_error = body.get("error") if isinstance(body, dict) else None
    if isinstance(api_error, dict):
        status = int(api_error.get("code") or 0)
        message = str(api_error.get("message") or "Gemini error")
        raise GeminiError(message, code="unknown", http_status=status or None)

    text = gemini_client._extract_text(body)
    return text, body if isinstance(body, dict) else {}
