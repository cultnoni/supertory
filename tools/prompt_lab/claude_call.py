"""Thin Anthropic Messages API caller for the prompt lab.

Reads ANTHROPIC_API_KEY from the repo-root .env. Never logs or returns the key.
Uses the anthropic SDK when importable; otherwise urllib. Does not install packages.
JSON is produced by prompt instruction and parsed with gemini_call.parse_model_json.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from env_loader import discover_env_paths, get_env, load_all_dotenv

load_all_dotenv()

from gemini_call import parse_model_json  # noqa: E402

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_TOKENS = 4096
THINKING_ON_MAX_TOKENS = 16000
MAX_RETRIES = 3
SAMPLING_LOCKED_MODELS = frozenset({"claude-sonnet-5", "claude-opus-5"})
_PLACEHOLDER_KEYS = frozenset({"your_anthropic_api_key_here", "changeme"})


def is_sampling_locked_model(model: str) -> bool:
    return str(model or "").strip() in SAMPLING_LOCKED_MODELS


def claude_max_tokens(thinking: str) -> int:
    return THINKING_ON_MAX_TOKENS if str(thinking).strip().lower() == "on" else DEFAULT_MAX_TOKENS


def simplify_json_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop anyOf/null, integer enums, and unsupported constraints for a 400 retry."""
    if not isinstance(schema, dict):
        return None

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "anyOf" in node:
            alts = node.get("anyOf") or []
            obj = next(
                (alt for alt in alts if isinstance(alt, dict) and alt.get("type") == "object"),
                None,
            )
            chosen = obj or next(
                (alt for alt in alts if isinstance(alt, dict) and alt.get("type") != "null"),
                alts[0] if alts else {},
            )
            return walk(chosen)
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in {
                "description",
                "minimum",
                "maximum",
                "minItems",
                "maxItems",
                "minLength",
                "maxLength",
            }:
                continue
            if key == "enum" and node.get("type") in {"integer", "number"}:
                continue
            out[key] = walk(value)
        return out

    return walk(schema)


def _output_config(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(schema, dict) or not schema:
        return None
    return {"format": {"type": "json_schema", "schema": schema}}


class ClaudeError(RuntimeError):
    """Raised when the Anthropic API cannot complete a request."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "unknown",
        http_status: int | None = None,
        retry_after: float | None = None,
        retries: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "unknown")
        self.http_status = int(http_status) if http_status is not None else None
        self.retry_after = float(retry_after) if retry_after is not None else None
        self.retries = int(retries or 0)


def clean_secret(raw: str | None) -> str | None:
    """Strip wrapping quotes, CR/LF, and surrounding whitespace. Never log the value."""
    if raw is None:
        return None
    text = str(raw).replace("\r", "").replace("\n", "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    text = text.replace("\r", "").replace("\n", "").strip()
    return text or None


def _read_dotenv_raw(name: str) -> str | None:
    for path in discover_env_paths():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for raw_line in text.splitlines(keepends=True):
            line = raw_line.replace("\r", "").replace("\n", "")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[7:].strip()
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                return value
        break
    return None


def anthropic_api_key() -> str | None:
    cleaned_file = clean_secret(_read_dotenv_raw("ANTHROPIC_API_KEY"))
    if cleaned_file:
        return cleaned_file
    return clean_secret(get_env("ANTHROPIC_API_KEY"))


def is_configured() -> bool:
    key = anthropic_api_key()
    return bool(key) and key not in _PLACEHOLDER_KEYS


def diagnose_anthropic_key() -> dict[str, Any]:
    """Metadata only. Never includes the secret value."""
    info: dict[str, Any] = {
        "exists": False,
        "length": 0,
        "starts_with_sk_ant": False,
        "leading_or_trailing_whitespace": False,
        "has_quotes": False,
        "has_cr": False,
        "has_lf": False,
    }
    for path in discover_env_paths():
        if not path.is_file():
            continue
        try:
            text = path.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError:
            break
        for raw_line in text.split("\n"):
            body = raw_line
            look = body.lstrip(" \t")
            if look.startswith("export "):
                look = look[7:].lstrip(" \t")
            if not look.startswith("ANTHROPIC_API_KEY="):
                continue
            value = look.split("=", 1)[1]
            info["exists"] = True
            info["has_cr"] = "\r" in value
            info["has_lf"] = "\n" in value
            info["leading_or_trailing_whitespace"] = value != value.strip(" \t")
            stripped = value.strip(" \t\r")
            info["has_quotes"] = any(ch in stripped[:1] + stripped[-1:] for ch in "'\"") if stripped else False
            info["length"] = len(value)
            cleaned = clean_secret(value) or ""
            info["starts_with_sk_ant"] = cleaned.startswith("sk-ant-")
            info["length_cleaned"] = len(cleaned)
            return info
        break
    return info


def generate(
    prompt: str,
    *,
    model: str,
    system: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: float = DEFAULT_TIMEOUT,
    thinking: str = "off",
    json_schema: dict[str, Any] | None = None,
    json_schema_simple: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call POST /v1/messages. Sonnet/Opus 5 omit sampling params; thinking off disables it.

    json_schema, if set, is sent as output_config.format type json_schema.
    A 400 on the full schema retries with json_schema_simple (or a stripped copy),
    then without structured output.
    """
    if not is_configured():
        raise ClaudeError(
            "Anthropic API 키가 없습니다. 프로젝트 폴더의 .env에 ANTHROPIC_API_KEY를 넣어 주세요.",
            code="auth",
        )
    chosen = str(model or "").strip()
    if not chosen:
        raise ClaudeError("모델 이름이 비어 있습니다.", code="unknown")

    thinking_on = str(thinking or "off").strip().lower() == "on"
    sampling_locked = is_sampling_locked_model(chosen)
    omit_temperature = bool(sampling_locked)
    thinking_disabled = bool(sampling_locked and not thinking_on)

    schema_attempts: list[tuple[str, dict[str, Any] | None]] = []
    if isinstance(json_schema, dict) and json_schema:
        schema_attempts.append(("full", json_schema))
        simple = json_schema_simple if isinstance(json_schema_simple, dict) and json_schema_simple else simplify_json_schema(json_schema)
        if simple and simple != json_schema:
            schema_attempts.append(("simple", simple))
    schema_attempts.append(("off", None))

    last_schema_error: str | None = None
    last_error: ClaudeError | None = None
    for schema_name, schema in schema_attempts:
        retries = 0
        while True:
            try:
                text, body = _generate_once(
                    prompt,
                    model=chosen,
                    system=system,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    omit_temperature=omit_temperature,
                    thinking_disabled=thinking_disabled,
                    output_config=_output_config(schema),
                )
                parsed, raw = parse_model_json(text)
                usage = _usage_from_body(body)
                stop_reason = body.get("stop_reason") if isinstance(body, dict) else None
                return {
                    "text": text,
                    "parsed": parsed,
                    "raw": raw if parsed is None else None,
                    "json_mime_used": False,
                    "model": chosen,
                    "empty": not bool((text or "").strip()),
                    "thinking_budget": None,
                    "thinking_disabled": thinking_disabled,
                    "thinking": "on" if thinking_on else "off",
                    "sampling_omitted": sampling_locked,
                    "finish_reason": stop_reason,
                    "stop_reason": stop_reason,
                    "truncated": str(stop_reason or "").lower() == "max_tokens",
                    "usage": usage,
                    "usage_metadata": usage,
                    "temperature_omitted": omit_temperature,
                    "retries": retries,
                    "candidate_count": 1,
                    "prompt_feedback": None,
                    "structured_output": schema_name,
                    "structured_reject_message": last_schema_error,
                }
            except ClaudeError as error:
                last_error = error
                if error.http_status == 400 and not omit_temperature and not sampling_locked:
                    omit_temperature = True
                    continue
                if (
                    error.http_status == 400
                    and schema is not None
                    and schema_name != "off"
                ):
                    last_schema_error = str(error)[:300]
                    break
                retryable = error.http_status in {429, 529} or error.code in {
                    "rate_limit",
                    "overloaded",
                }
                if retryable and retries < MAX_RETRIES:
                    retries += 1
                    wait = error.retry_after if error.retry_after and error.retry_after > 0 else 5.0 * retries
                    time.sleep(min(60.0, float(wait)))
                    continue
                error.retries = retries
                raise
        if last_schema_error:
            continue
    assert last_error is not None
    last_error.retries = getattr(last_error, "retries", 0) or 0
    raise last_error


def _usage_from_body(body: dict[str, Any] | None) -> dict[str, int]:
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": _as_int(usage.get("input_tokens")),
        "output_tokens": _as_int(usage.get("output_tokens")),
    }


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _payload(
    *,
    model: str,
    prompt: str,
    system: str | None,
    temperature: float,
    max_tokens: int,
    omit_temperature: bool,
    thinking_disabled: bool,
    output_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max(1, int(max_tokens or DEFAULT_MAX_TOKENS)),
        "messages": [{"role": "user", "content": (prompt or "").strip()}],
    }
    if system:
        payload["system"] = system
    if thinking_disabled:
        payload["thinking"] = {"type": "disabled"}
    if not omit_temperature:
        payload["temperature"] = float(temperature)
    if output_config:
        payload["output_config"] = output_config
    return payload


def _generate_once(
    prompt: str,
    *,
    model: str,
    system: str | None,
    temperature: float,
    max_tokens: int,
    timeout: float,
    omit_temperature: bool,
    thinking_disabled: bool,
    output_config: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    payload = _payload(
        model=model,
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        omit_temperature=omit_temperature,
        thinking_disabled=thinking_disabled,
        output_config=output_config,
    )
    try:
        import anthropic  # type: ignore

        return _generate_sdk(payload, timeout=timeout)
    except ImportError:
        return _generate_urllib(payload, timeout=timeout)


def _generate_sdk(payload: dict[str, Any], *, timeout: float) -> tuple[str, dict[str, Any]]:
    import anthropic  # type: ignore

    api_key = anthropic_api_key()
    client = anthropic.Anthropic(api_key=api_key, timeout=float(timeout or DEFAULT_TIMEOUT), max_retries=0)
    try:
        resp = client.messages.create(**payload)
    except TypeError as error:
        if payload.get("output_config"):
            raise ClaudeError(
                f"SDK가 output_config를 받지 않습니다: {str(error)[:200]}",
                code="bad_request",
                http_status=400,
            ) from None
        raise ClaudeError(_short_api_message(error, "요청을 처리할 수 없습니다."), code="bad_request", http_status=400) from None
    except anthropic.RateLimitError as error:
        raise ClaudeError(
            "Anthropic 호출 한도입니다.",
            code="rate_limit",
            http_status=getattr(error, "status_code", None) or 429,
            retry_after=_retry_after_from_exc(error),
        ) from None
    except anthropic.OverloadedError as error:
        raise ClaudeError(
            "Anthropic 서버가 과부하입니다.",
            code="overloaded",
            http_status=getattr(error, "status_code", None) or 529,
            retry_after=_retry_after_from_exc(error),
        ) from None
    except anthropic.AuthenticationError as error:
        raise ClaudeError(
            "Anthropic API 키가 유효하지 않습니다.",
            code="auth",
            http_status=getattr(error, "status_code", None) or 401,
        ) from None
    except anthropic.BadRequestError as error:
        raise ClaudeError(
            _short_api_message(error, "요청을 처리할 수 없습니다."),
            code="bad_request",
            http_status=getattr(error, "status_code", None) or 400,
        ) from None
    except anthropic.APITimeoutError as error:
        raise ClaudeError("Anthropic 응답 시간이 초과되었습니다.", code="timeout") from None
    except anthropic.APIStatusError as error:
        status = int(getattr(error, "status_code", None) or 0) or None
        code = "rate_limit" if status == 429 else ("overloaded" if status == 529 else "unknown")
        raise ClaudeError(
            _short_api_message(error, "Anthropic 호출에 실패했습니다."),
            code=code,
            http_status=status,
            retry_after=_retry_after_from_exc(error),
        ) from None
    except anthropic.APIError as error:
        raise ClaudeError(
            _short_api_message(error, "Anthropic 호출에 실패했습니다."),
            code="unknown",
        ) from None

    texts: list[str] = []
    for block in getattr(resp, "content", None) or []:
        if getattr(block, "type", None) == "text":
            texts.append(getattr(block, "text", "") or "")
    usage_obj = getattr(resp, "usage", None)
    usage = {
        "input_tokens": _as_int(getattr(usage_obj, "input_tokens", 0)),
        "output_tokens": _as_int(getattr(usage_obj, "output_tokens", 0)),
    }
    body = {
        "stop_reason": getattr(resp, "stop_reason", None),
        "usage": usage,
        "id": getattr(resp, "id", None),
    }
    return "".join(texts), body


def _generate_urllib(payload: dict[str, Any], *, timeout: float) -> tuple[str, dict[str, Any]]:
    api_key = anthropic_api_key()
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "x-api-key": api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )
    wait = max(5.0, float(timeout or DEFAULT_TIMEOUT))
    try:
        with urllib.request.urlopen(request, timeout=wait) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        status = int(error.code)
        retry_after = _retry_after_from_headers(error)
        code = "rate_limit" if status == 429 else ("overloaded" if status == 529 else "unknown")
        if status == 400:
            code = "bad_request"
        raise ClaudeError(
            _short_http_message(status, detail),
            code=code,
            http_status=status,
            retry_after=retry_after,
        ) from None
    except (TimeoutError, urllib.error.URLError, OSError):
        raise ClaudeError("Anthropic 응답 시간이 초과되었거나 네트워크 오류입니다.", code="timeout") from None

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise ClaudeError("Anthropic 응답을 해석하지 못했습니다.", code="unknown") from None
    if not isinstance(body, dict):
        raise ClaudeError("Anthropic 응답 형식이 올바르지 않습니다.", code="unknown")
    api_error = body.get("error")
    if isinstance(api_error, dict):
        status = int(api_error.get("status") or 0) or None
        raise ClaudeError(
            _short_http_message(status or 0, json.dumps(api_error, ensure_ascii=False)),
            code="unknown",
            http_status=status,
        )
    return _extract_text(body), body


def _extract_text(body: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in body.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def _retry_after_from_headers(error: urllib.error.HTTPError) -> float | None:
    try:
        header = error.headers.get("Retry-After") if error.headers else None
    except Exception:
        header = None
    return _parse_retry_after(header)


def _retry_after_from_exc(error: Any) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        header = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        header = None
    return _parse_retry_after(header)


def _parse_retry_after(header: Any) -> float | None:
    if header is None:
        return None
    try:
        return float(str(header).strip())
    except (TypeError, ValueError):
        return None


def _short_api_message(error: Any, fallback: str) -> str:
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        msg = err.get("message") if isinstance(err, dict) else None
        if msg:
            return str(msg)[:300]
    message = getattr(error, "message", None)
    if message:
        return str(message)[:300]
    return fallback


def _short_http_message(status: int, detail: str) -> str:
    msg = ""
    try:
        parsed = json.loads(detail) if detail else {}
        if isinstance(parsed, dict):
            err = parsed.get("error") if isinstance(parsed.get("error"), dict) else parsed
            if isinstance(err, dict):
                msg = str(err.get("message") or "")
    except json.JSONDecodeError:
        msg = ""
    if msg:
        return msg[:300]
    if status:
        return f"HTTP {status}"
    return "Anthropic 호출에 실패했습니다."
