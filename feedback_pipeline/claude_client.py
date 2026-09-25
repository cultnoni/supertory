"""Anthropic Messages API 호출. 키 값은 출력·저장·로그에 남기지 않는다."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from env_loader import apply_bundled_defaults, get_env, load_all_dotenv

load_all_dotenv()
apply_bundled_defaults()

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_TOKENS = 4096
MAX_RETRIES = 3
SAMPLING_LOCKED_MODELS = frozenset({"claude-sonnet-5", "claude-opus-5"})
_PLACEHOLDER_KEYS = frozenset({"your_anthropic_api_key_here", "changeme"})

# 공식 가격표 확인 필요 (Anthropic 공개 단가, 입력/출력 100만 토큰당 USD).
PRICE_SONNET_USD = (3.0, 15.0)
PRICE_HAIKU_45_USD = (1.0, 5.0)
PRICE_PER_MILLION_USD: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": PRICE_SONNET_USD,
    "claude-haiku-4-5": PRICE_HAIKU_45_USD,
    "claude-haiku-4-5-20251001": PRICE_HAIKU_45_USD,
}
# 5분 ephemeral 캐시. 쓰기는 입력의 1.25배, 읽기는 0.1배.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.1

GenerateFn = Callable[..., dict[str, Any]]


class ClaudeError(RuntimeError):
    """Anthropic 호출이 끝나지 못했을 때."""

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


def estimate_cost_parts(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> dict[str, float]:
    """일반 입력 / 캐시 쓰기 / 캐시 읽기 / 출력 비용을 나눈다."""
    rates = PRICE_PER_MILLION_USD.get(str(model or "").strip(), PRICE_SONNET_USD)
    inp, out = rates
    input_usd = max(0, int(input_tokens)) / 1_000_000.0 * inp
    cache_write_usd = max(0, int(cache_write_tokens)) / 1_000_000.0 * inp * CACHE_WRITE_MULTIPLIER
    cache_read_usd = max(0, int(cache_read_tokens)) / 1_000_000.0 * inp * CACHE_READ_MULTIPLIER
    output_usd = max(0, int(output_tokens)) / 1_000_000.0 * out
    return {
        "input_usd": round(input_usd, 6),
        "cache_write_usd": round(cache_write_usd, 6),
        "cache_read_usd": round(cache_read_usd, 6),
        "output_usd": round(output_usd, 6),
        "total_usd": round(input_usd + cache_write_usd + cache_read_usd + output_usd, 6),
    }


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """100만 토큰당 단가로 추정 비용을 계산한다. 캐시 쓰기·읽기 요금을 포함한다."""
    return float(
        estimate_cost_parts(
            model,
            input_tokens,
            output_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_read_tokens=cache_read_tokens,
        )["total_usd"]
    )


def parse_model_json(raw: str) -> tuple[Any | None, str]:
    """JSON 객체 파싱. 펜스와 앞뒤 잡음은 한 번 벗겨 본다."""
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


def is_sampling_locked_model(model: str) -> bool:
    return str(model or "").strip() in SAMPLING_LOCKED_MODELS


def simplify_json_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """400 재시도용으로 anyOf/null·숫자 enum·제약 조건을 뺀다."""
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


def clean_secret(raw: str | None) -> str | None:
    """따옴표·개행·공백을 벗긴다. 값은 로그하지 않는다."""
    if raw is None:
        return None
    text = str(raw).replace("\r", "").replace("\n", "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    text = text.replace("\r", "").replace("\n", "").strip()
    return text or None


def anthropic_api_key() -> str | None:
    """GEMINI_API_KEY와 같이 .env → 환경변수 → bundled_env 순으로 읽는다."""
    return clean_secret(get_env("ANTHROPIC_API_KEY"))


_forced_configured: bool | None = None
_injected_client: Any | None = None


def is_fake_mode() -> bool:
    """SUPERTORY_FEEDBACK_FAKE=1 이면 실제 Claude를 부르지 않는다."""
    raw = str(get_env("SUPERTORY_FEEDBACK_FAKE") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _fake_card_delay() -> float:
    raw = str(get_env("SUPERTORY_FEEDBACK_FAKE_DELAY") or "").strip()
    if not raw:
        return 0.7
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.7


def is_configured() -> bool:
    if _forced_configured is not None:
        return bool(_forced_configured)
    if is_fake_mode():
        return True
    key = anthropic_api_key()
    return bool(key) and key not in _PLACEHOLDER_KEYS


def set_configured_for_tests(value: bool | None) -> None:
    """테스트에서만 설정 여부를 고정한다. None이면 실제 키 검사를 쓴다."""
    global _forced_configured
    _forced_configured = None if value is None else bool(value)


def get_client() -> Any:
    """서버·러너가 쓰는 Claude 클라이언트. 테스트는 set_client_for_tests로 바꾼다."""
    if _injected_client is not None:
        return _injected_client
    if is_fake_mode():
        return UiFakeClaude(delay=_fake_card_delay())
    return LiveClaude()


def set_client_for_tests(client: Any | None) -> None:
    """테스트용 클라이언트를 주입한다. None이면 LiveClaude로 되돌린다."""
    global _injected_client
    _injected_client = client


def generate(
    prompt: str,
    *,
    model: str,
    system: str | list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: float = DEFAULT_TIMEOUT,
    thinking: str = "off",
    json_schema: dict[str, Any] | None = None,
    json_schema_simple: dict[str, Any] | None = None,
    cached_prefix: str | None = None,
) -> dict[str, Any]:
    """POST /v1/messages. sonnet-5는 thinking 끄고 temperature를 보내지 않는다.

    system이 content block 목록이면 그대로 보낸다(문학 캐시 접두용).
    cached_prefix가 있으면 유저 메시지 앞에 ephemeral 캐시 블록을 붙인다.
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
        simple = (
            json_schema_simple
            if isinstance(json_schema_simple, dict) and json_schema_simple
            else simplify_json_schema(json_schema)
        )
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
                    cached_prefix=cached_prefix,
                )
                parsed, raw = parse_model_json(text)
                usage = _usage_from_body(body)
                stop_reason = body.get("stop_reason") if isinstance(body, dict) else None
                return {
                    "text": text,
                    "parsed": parsed,
                    "raw": raw if parsed is None else None,
                    "model": chosen,
                    "empty": not bool((text or "").strip()),
                    "thinking_disabled": thinking_disabled,
                    "stop_reason": stop_reason,
                    "truncated": str(stop_reason or "").lower() == "max_tokens",
                    "usage": usage,
                    "retries": retries,
                    "structured_output": schema_name,
                    "structured_reject_message": last_schema_error,
                }
            except ClaudeError as error:
                last_error = error
                if error.http_status == 400 and not omit_temperature and not sampling_locked:
                    omit_temperature = True
                    continue
                if error.http_status == 400 and schema is not None and schema_name != "off":
                    last_schema_error = str(error)[:300]
                    break
                retryable = error.http_status in {429, 529} or error.code in {
                    "rate_limit",
                    "overloaded",
                }
                if retryable and retries < MAX_RETRIES:
                    retries += 1
                    wait = (
                        error.retry_after
                        if error.retry_after and error.retry_after > 0
                        else 5.0 * retries
                    )
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
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    return {
        "input_tokens": _as_int(usage.get("input_tokens")),
        "output_tokens": _as_int(usage.get("output_tokens")),
        "cache_creation_input_tokens": _as_int(usage.get("cache_creation_input_tokens")),
        "cache_read_input_tokens": _as_int(usage.get("cache_read_input_tokens")),
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
    system: str | list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int,
    omit_temperature: bool,
    thinking_disabled: bool,
    output_config: dict[str, Any] | None = None,
    cached_prefix: str | None = None,
) -> dict[str, Any]:
    # 문학 캐시는 system content blocks에 둔다. 유저 쪽 cached_prefix는
    # 예전 경로·비문학 호출용 호환이다. system에 이미 cache_control이 있으면
    # 유저 prefix를 붙여 이중 쓰기가 나지 않게 한다.
    system_has_cache = False
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("cache_control"):
                system_has_cache = True
                break
    prefix = "" if system_has_cache else str(cached_prefix or "").strip()
    if prefix:
        content: str | list[dict[str, Any]] = [
            {
                "type": "text",
                "text": prefix,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": (prompt or "").strip() or "(지시 없음)"},
        ]
    else:
        content = (prompt or "").strip() or "(지시 없음)"
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max(1, int(max_tokens or DEFAULT_MAX_TOKENS)),
        "messages": [{"role": "user", "content": content}],
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
    system: str | list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int,
    timeout: float,
    omit_temperature: bool,
    thinking_disabled: bool,
    output_config: dict[str, Any] | None = None,
    cached_prefix: str | None = None,
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
        cached_prefix=cached_prefix,
    )
    try:
        import anthropic  # type: ignore

        return _generate_sdk(payload, timeout=timeout)
    except ImportError:
        return _generate_urllib(payload, timeout=timeout)


def _generate_sdk(payload: dict[str, Any], *, timeout: float) -> tuple[str, dict[str, Any]]:
    import anthropic  # type: ignore

    api_key = anthropic_api_key()
    client = anthropic.Anthropic(
        api_key=api_key, timeout=float(timeout or DEFAULT_TIMEOUT), max_retries=0
    )
    try:
        resp = client.messages.create(**payload)
    except TypeError as error:
        if payload.get("output_config"):
            raise ClaudeError(
                f"SDK가 output_config를 받지 않습니다: {str(error)[:200]}",
                code="bad_request",
                http_status=400,
            ) from None
        raise ClaudeError(
            _short_api_message(error, "요청을 처리할 수 없습니다."),
            code="bad_request",
            http_status=400,
        ) from None
    except anthropic.RateLimitError as error:
        raise ClaudeError(
            "Anthropic 호출 한도입니다.",
            code="rate_limit",
            http_status=getattr(error, "status_code", None) or 429,
            retry_after=_retry_after_from_exc(error),
        ) from None
    except anthropic.APIStatusError as error:
        status = int(getattr(error, "status_code", None) or 0) or None
        code = "rate_limit" if status == 429 else ("overloaded" if status == 529 else "unknown")
        if status == 400:
            code = "bad_request"
        if status in {401, 403}:
            code = "auth"
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
        "cache_creation_input_tokens": _as_int(getattr(usage_obj, "cache_creation_input_tokens", 0)),
        "cache_read_input_tokens": _as_int(getattr(usage_obj, "cache_read_input_tokens", 0)),
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
        raise ClaudeError(
            "Anthropic 응답 시간이 초과되었거나 네트워크 오류입니다.", code="timeout"
        ) from None

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise ClaudeError("Anthropic 응답을 해석하지 못했습니다.", code="unknown") from None
    if not isinstance(body, dict):
        raise ClaudeError("Anthropic 응답 형식이 올바르지 않습니다.", code="unknown")
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


BATCH_URL = "https://api.anthropic.com/v1/messages/batches"


def message_batch_body(requests: list[dict[str, Any]]) -> dict[str, Any]:
    """측정 스크립트가 Batch API에 넣는 요청 본문. 앱 실시간 호출은 쓰지 않는다."""
    items = []
    for index, item in enumerate(requests):
        params = dict(item.get("params") or item)
        params.pop("custom_id", None)
        items.append(
            {
                "custom_id": str(item.get("custom_id") or f"req-{index}"),
                "params": params,
            }
        )
    return {"requests": items}


def submit_message_batch(requests: list[dict[str, Any]], *, timeout: float = 60.0) -> str:
    """Batch API에 넣고 배치 id를 돌려준다. 실시간 messages.create는 부르지 않는다."""
    if not is_configured():
        raise ClaudeError("Anthropic API 키가 없습니다.", code="auth")
    api_key = anthropic_api_key()
    data = json.dumps(message_batch_body(requests), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BATCH_URL,
        data=data,
        headers={
            "x-api-key": api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(5.0, float(timeout))) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ClaudeError(_short_http_message(int(error.code), detail), code="bad_request", http_status=int(error.code)) from None
    batch_id = str((body or {}).get("id") or "")
    if not batch_id:
        raise ClaudeError("Batch API가 id를 주지 않았습니다.", code="unknown")
    return batch_id


class BatchClaude:
    """측정용. generate 한 번을 요청 1개인 배치로 보내고 끝날 때까지 기다린다."""

    def generate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        model = str(kwargs.get("model") or "")
        payload = _payload(
            model=model,
            prompt=prompt,
            system=kwargs.get("system"),
            temperature=float(kwargs.get("temperature") or 0.2),
            max_tokens=int(kwargs.get("max_tokens") or DEFAULT_MAX_TOKENS),
            omit_temperature=is_sampling_locked_model(model),
            thinking_disabled=is_sampling_locked_model(model),
            output_config=_output_config(kwargs.get("json_schema")),
            cached_prefix=kwargs.get("cached_prefix"),
        )
        batch_id = submit_message_batch([{"custom_id": "call", "params": payload}])
        # Batch는 수분 걸릴 수 있다. 측정용 기본 대기는 30분.
        deadline = time.time() + max(float(kwargs.get("timeout") or 0), 1800.0)
        while time.time() < deadline:
            status, results = _poll_message_batch(batch_id)
            if status == "ended":
                return _result_from_batch_item(results[0] if results else {}, model)
            time.sleep(2.0)
        raise ClaudeError("Batch API 대기 시간이 끝났습니다.", code="timeout")


def _poll_message_batch(batch_id: str) -> tuple[str, list[dict[str, Any]]]:
    api_key = anthropic_api_key()
    request = urllib.request.Request(
        f"{BATCH_URL}/{batch_id}",
        headers={"x-api-key": api_key or "", "anthropic-version": ANTHROPIC_VERSION},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = json.loads(response.read().decode("utf-8"))
    status = str(body.get("processing_status") or "")
    if status != "ended":
        return status, []
    result_request = urllib.request.Request(
        f"{BATCH_URL}/{batch_id}/results",
        headers={"x-api-key": api_key or "", "anthropic-version": ANTHROPIC_VERSION},
        method="GET",
    )
    with urllib.request.urlopen(result_request, timeout=60) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    rows = []
    for line in raw.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return status, rows


def _result_from_batch_item(item: dict[str, Any], model: str) -> dict[str, Any]:
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    if result.get("type") != "succeeded":
        raise ClaudeError(str(result.get("error") or "Batch 요청이 실패했습니다.")[:300], code="unknown")
    message = result.get("message") if isinstance(result.get("message"), dict) else {}
    text = _extract_text(message)
    parsed, raw = parse_model_json(text)
    return {
        "text": text,
        "parsed": parsed,
        "raw": raw if parsed is None else None,
        "model": model,
        "empty": not bool((text or "").strip()),
        "usage": _usage_from_body(message),
        "stop_reason": message.get("stop_reason"),
        "truncated": str(message.get("stop_reason") or "").lower() == "max_tokens",
    }


class LiveClaude:
    """실제 generate를 감싼다. 테스트에서 FakeClaude로 바꿀 수 있다."""

    def generate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        return generate(prompt, **kwargs)


class FakeClaude:
    """고정 응답. 프롬프트를 기록하고 큐에서 꺼내거나 기본 JSON을 돌려준다."""

    def __init__(
        self,
        responses: list[Any] | None = None,
        *,
        default_parsed: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.prompts: list[str] = []
        self.kwargs: list[dict[str, Any]] = []
        self._queue: list[Any] = list(responses or [])
        self.default_parsed = default_parsed
        self.usage = usage or {"input_tokens": 10, "output_tokens": 20}
        self._lock = threading.Lock()

    def generate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.prompts.append(prompt)
            self.kwargs.append(kwargs)
            if self._queue:
                item = self._queue.pop(0)
            else:
                item = None
        if item is None:
            if self.default_parsed is not None:
                return self._as_result(self.default_parsed)
            raise ClaudeError("FakeClaude에 남은 응답이 없습니다.", code="empty")
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return {
                "text": item,
                "parsed": None,
                "raw": None,
                "model": "claude-sonnet-5",
                "empty": not bool(item.strip()),
                "thinking_disabled": True,
                "stop_reason": "end_turn",
                "truncated": False,
                "usage": dict(self.usage),
                "retries": 0,
                "structured_output": "off",
                "structured_reject_message": None,
            }
        return self._as_result(item)

    def _as_result(self, item: Any) -> dict[str, Any]:
        if isinstance(item, dict) and "parsed" in item:
            parsed = item.get("parsed")
            text = item.get("text")
            if text is None:
                text = json.dumps(parsed, ensure_ascii=False) if parsed is not None else ""
            usage = item.get("usage") or self.usage
            if isinstance(item.get("error"), Exception):
                raise item["error"]
            return {
                "text": text,
                "parsed": parsed,
                "raw": item.get("raw"),
                "model": item.get("model") or "claude-sonnet-5",
                "empty": not bool(str(text or "").strip()),
                "thinking_disabled": True,
                "stop_reason": item.get("stop_reason") or "end_turn",
                "truncated": False,
                "usage": usage,
                "retries": 0,
                "structured_output": "full",
                "structured_reject_message": None,
            }
        parsed = item
        text = json.dumps(parsed, ensure_ascii=False)
        return {
            "text": text,
            "parsed": parsed,
            "raw": None,
            "model": "claude-sonnet-5",
            "empty": False,
            "thinking_disabled": True,
            "stop_reason": "end_turn",
            "truncated": False,
            "usage": dict(self.usage),
            "retries": 0,
            "structured_output": "full",
            "structured_reject_message": None,
        }


_PARA_LINE = re.compile(r"^\[P(\d+)\](?: \(제목\))?\s+(.*)$")


def _paras_from_prompt(prompt: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in str(prompt or "").splitlines():
        stripped = line.strip()
        if stripped == "(장면 구분선)":
            continue
        match = _PARA_LINE.match(stripped)
        if not match:
            continue
        text = str(match.group(2) or "").strip()
        if text:
            out.append((int(match.group(1)), text))
    return out


def _quote_slice(text: str, head: bool = True, width: int = 12) -> str:
    body = str(text or "").strip() or " "
    n = min(max(1, width), len(body))
    return body[:n] if head else body[-n:]


def _range_for(number: int, text: str) -> dict[str, Any]:
    body = str(text or "").strip() or " "
    n = min(12, len(body))
    return {
        "start_para": int(number),
        "end_para": int(number),
        "start_quote": body[:n],
        "end_quote": body[-n:],
    }


def _range_for_pair(start: tuple[int, str], end: tuple[int, str]) -> dict[str, Any]:
    s_num, s_text = start
    e_num, e_text = end
    if int(e_num) < int(s_num):
        s_num, s_text, e_num, e_text = e_num, e_text, s_num, s_text
    return {
        "start_para": int(s_num),
        "end_para": int(e_num),
        "start_quote": _quote_slice(s_text, True),
        "end_quote": _quote_slice(e_text, False),
    }


def _pick_para(paras: list[tuple[int, str]], index: int) -> tuple[int, str]:
    if not paras:
        return 1, "시험용 문단입니다."
    return paras[index % len(paras)]


def _spread_indices(count: int, slots: int) -> list[int]:
    if count <= 0:
        return [0] * slots
    if count >= slots:
        return [min(count - 1, i * count // slots) for i in range(slots)]
    return [i % count for i in range(slots)]


def _snippet(text: str, width: int = 12) -> str:
    body = re.sub(r"\s+", " ", str(text or "").strip())
    if not body:
        return "이 부분"
    if len(body) <= width:
        return body
    return body[:width].rstrip()


_SENT_SPLIT = re.compile(r"(?<=[.!?。…])\s+")

_FILLERS = ("아주", "매우", "살짝", "간신히", "조금", "이미", "오래", "달랑")

_REASON_TEMPLATES: dict[str, list[str]] = {
    "explain_less": [
        "‘{q}’처럼 감정을 바로 설명해 버립니다. 독자가 장면을 스스로 읽을 틈이 줄어듭니다. 그 문장을 빼거나 행동만 남기면 더 선명해집니다.",
        "‘{q}’처럼 속마음을 단정하는 문장이 이어집니다. 독자는 이미 눈치챘는데 한 번 더 듣게 됩니다. 해설을 줄이고 동작만 남기는 편이 낫습니다.",
        "‘{q}’처럼 해설이 장면을 정리해 버립니다. 여운이 설명으로 바뀌어 몰입이 옅어집니다. 마지막 설명을 덜어 내면 숨이 살아납니다.",
        "‘{q}’처럼 의미를 말로 붙입니다. 독자는 해석할 자리를 잃습니다. 행동과 대사만 남기면 읽는 속도가 살아납니다.",
        "‘{q}’처럼 감정 이름을 바로 적었습니다. 보여 주기보다 알려 주기에 가깝습니다. 한 줄을 덜면 장면이 더 또렷해집니다.",
    ],
    "action_clarity": [
        "‘{q}’처럼 누가 먼저 움직였는지 겹쳐 보입니다. 독자가 동작을 재구성해야 합니다. 순서를 한 줄로 나누면 따라가기 쉽습니다.",
        "‘{q}’처럼 동작이 한 문장에 몰려 있습니다. 누가 무엇을 했는지 한 박자 늦습니다. 주어와 동작을 나눠 적으면 선명해집니다.",
        "‘{q}’처럼 움직임이 동시에 적혀 혼선이 납니다. 화면이 겹쳐 보입니다. 시간 순으로 한 동작씩 적으면 낫습니다.",
        "‘{q}’처럼 손과 시선이 한꺼번에 움직입니다. 독자가 컷을 놓칩니다. 한 동작을 먼저 보여 주면 읽기 쉽습니다.",
        "‘{q}’처럼 인과가 뒤섞여 있습니다. 무엇이 원인인지 한 번 더 생각하게 됩니다. 순서를 바로잡으면 장면이 안정됩니다.",
    ],
    "redundancy": [
        "‘{q}’처럼 같은 정보가 한 번 더 나옵니다. 독자는 이미 아는 말을 다시 듣습니다. 두 번째 구절을 빼면 호흡이 가벼워집니다.",
        "‘{q}’처럼 앞에서 본 설명이 반복됩니다. 같은 이미지가 두 번 겹칩니다. 뒤의 반복만 지우면 충분합니다.",
        "‘{q}’처럼 비슷한 말이 연달아 붙습니다. 강조가 아니라 군더더기로 읽힙니다. 한 번만 남기면 문장이 팽팽해집니다.",
        "‘{q}’처럼 같은 뜻을 다른 말로 되풀이합니다. 읽는 속도가 느려집니다. 반복된 조각을 덜어 내세요.",
        "‘{q}’처럼 정보가 겹쳐 있습니다. 독자가 같은 사실을 두 번 처리합니다. 나중 구절을 빼는 편이 낫습니다.",
    ],
    "reader_question": [
        "‘{q}’처럼 앞머리가 설명으로 시작합니다. 독자는 왜 그런지부터 묻게 됩니다. 설명 구를 빼고 장면부터 보여 주세요.",
        "‘{q}’처럼 동기를 미리 풀어 놓습니다. 궁금증이 생기기 전에 답이 나옵니다. 앞 설명을 덜면 질문이 살아납니다.",
        "‘{q}’처럼 이유를 먼저 말해 버립니다. 독자가 따라갈 자리가 없습니다. 도입 설명을 지우면 호흡이 당깁니다.",
        "‘{q}’처럼 배경부터 늘어놓습니다. 장면보다 해설이 먼저 보입니다. 앞머리를 덜고 행동부터 적으세요.",
        "‘{q}’처럼 독자가 물을 틈이 없습니다. 설명이 길을 막아 섭니다. 앞 구절을 빼면 호기심이 남습니다.",
    ],
    "abstract_expression": [
        "‘{q}’처럼 형용사·부사가 감정을 대신합니다. 독자는 느낌을 전달받지만 장면은 흐릿합니다. 수식어를 덜거나 바꿔 보세요.",
        "‘{q}’처럼 추상적인 말이 장면을 덮습니다. 무엇이 보였는지보다 어떤 느낌인지가 앞섭니다. 수식 한두 개를 빼면 또렷해집니다.",
        "‘{q}’처럼 감각이 말로만 남아 있습니다. 손이 아니라 분위기가 먼저 옵니다. 꾸밈말을 구체 동작으로 바꾸세요.",
        "‘{q}’처럼 막연한 표현이 이어집니다. 독자가 그림을 그리기 어렵습니다. 형용사를 덜면 윤곽이 살아납니다.",
        "‘{q}’처럼 정도를 나타내는 말이 겹칩니다. 강조가 흐려집니다. 부사 하나만이라도 지우면 문장이 단단해집니다.",
    ],
    "other": [
        "‘{q}’처럼 문체가 한 박자 흔들립니다. 바로 앞 문장과 온도가 달라 독자가 멈칫합니다. 손질은 선택이고, 이유만 남겨 둡니다.",
        "‘{q}’처럼 말맛이 갑자기 바뀝니다. 같은 인물의 호흡이 다르게 들립니다. 수정안 없이 확인해 달라는 표시입니다.",
        "‘{q}’처럼 문장 결이 어긋납니다. 틀린 것은 아니나 거슬릴 수 있습니다. 고치지 않고 참고만 해 주세요.",
        "‘{q}’처럼 시선이 살짝 튑니다. 독자가 누구 쪽인지 한 번 더 묻게 됩니다. 수정안은 두지 않았습니다.",
        "‘{q}’처럼 어조가 한 줄만 다릅니다. 큰 문제는 아닙니다. 이유만 적고 문장은 그대로 둡니다.",
    ],
    "info_placement": [
        "‘{q}’처럼 설정 설명이 장면 한가운데 끼어 있습니다. 지금 필요한 정보인지 독자가 헷갈립니다. 이 블록을 뒤 장면으로 옮겨 보세요.",
        "‘{q}’처럼 세계 설명이 행동보다 먼저 옵니다. 몰입이 설명으로 끊깁니다. 위치만 바꾸면 되고 문장 수정안은 없습니다.",
        "‘{q}’처럼 정보가 이 자리에선 무겁습니다. 독자는 아직 물을 준비가 안 됐습니다. 배치만 미루는 편이 낫습니다.",
        "‘{q}’처럼 배경 지식이 대화를 가로챕니다. 장면의 온도가 식습니다. 문장은 두고 위치만 조정하세요.",
        "‘{q}’처럼 설명이 클라이맥스 앞에 놓여 있습니다. 긴장이 풀립니다. 수정안 없이 배치만 제안합니다.",
    ],
    "consistency": [
        "‘{q}’가 앞에서 쓴 호칭과 맞는지 확인해 주세요. 같은 인물이라면 독자가 두 사람을 헷갈릴 수 있습니다. 맞다면 그대로 두어도 됩니다.",
        "‘{q}’가 설정집의 이름과 같은지 한 번만 봐 주세요. 다를 수도 있고, 일부러 바꾼 것일 수도 있습니다. 맞는 쪽을 알려 주시면 됩니다.",
        "‘{q}’가 이전 회차 표기와 어긋나 보일 수 있습니다. 독자가 오타로 읽을 여지가 있습니다. 의도인지 확인해 주세요.",
        "‘{q}’가 직전 문단의 호칭과 다르게 들립니다. 같은 대상인지 확신이 없습니다. 맞다면 수정할 필요는 없습니다.",
        "‘{q}’가 인물 표기와 충돌하는지 확인이 필요합니다. 확실하지 않아 수정안은 비워 두었습니다. 맞는지 알려 주세요.",
    ],
}


def _reason_for(style: str, original: str, slot: int) -> str:
    templates = _REASON_TEMPLATES.get(style) or _REASON_TEMPLATES["other"]
    template = templates[slot % len(templates)]
    return template.format(q=_snippet(original))


def _sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(str(text or "").strip())
    return [p for p in parts if p]


def _length_ok(original: str, suggestion: str, limit: float = 0.30) -> bool:
    orig = len(original or "")
    if orig <= 0:
        return bool(suggestion)
    return abs(len(suggestion) - orig) / orig <= limit


def _drop_last_sentence(text: str) -> str:
    sents = _sentences(text)
    if len(sents) >= 2:
        rest = " ".join(sents[:-1]).strip()
        if rest and _length_ok(text, rest):
            return rest
    cut = max(1, min(int(max(len(text), 1) * 0.25), 10))
    if len(text) > cut + 4:
        return text[:-cut].rstrip(" ,，")
    return text


def _drop_second_repeat(text: str) -> str:
    body = str(text or "")
    for width in (6, 5, 4, 3, 2):
        for i in range(0, max(0, len(body) - width)):
            piece = body[i : i + width]
            if not piece.strip() or piece.isspace():
                continue
            found = body.find(piece, i + width)
            if found >= 0:
                candidate = body[:found] + body[found + width :]
                if candidate != body and _length_ok(body, candidate):
                    return candidate
    return _drop_last_sentence(body)


def _drop_fillers(text: str) -> str:
    body = str(text or "")
    for word in _FILLERS:
        if word in body:
            candidate = body.replace(word, "", 1)
            candidate = re.sub(r" {2,}", " ", candidate).strip()
            if candidate and candidate != body and _length_ok(body, candidate):
                return candidate
    return _drop_last_sentence(body)


def _drop_leading_clause(text: str) -> str:
    body = str(text or "").strip()
    for sep in ("，", ",", " — ", " - ", "는 ", "은 "):
        if sep in body:
            head, tail = body.split(sep, 1)
            tail = tail.strip()
            if tail and len(head) >= 2 and _length_ok(body, tail):
                return tail
    sents = _sentences(body)
    if len(sents) >= 2:
        rest = " ".join(sents[1:]).strip()
        if rest and _length_ok(body, rest):
            return rest
    return _drop_last_sentence(body)


def _clarity_tweak(text: str) -> str:
    body = str(text or "")
    if "그리고 " in body:
        candidate = body.replace("그리고 ", "", 1)
        if candidate != body and _length_ok(body, candidate):
            return candidate
    sents = _sentences(body)
    if len(sents) >= 2:
        swapped = " ".join([sents[-1]] + sents[:-1]).strip()
        if swapped != body and _length_ok(body, swapped):
            return swapped
    return _drop_fillers(body)


def _short_for_warning(text: str) -> str:
    body = str(text or "").strip()
    sents = _sentences(body)
    if sents:
        first = sents[0]
        if len(first) < max(8, int(len(body) * 0.35)):
            return first
    return body[: max(4, min(8, len(body) // 4))] or "다."


def _ensure_changed(original: str, suggestion: str, salt: str) -> str:
    orig = str(original or "")
    sug = str(suggestion or "")
    if sug and sug != orig:
        return sug
    if len(orig) > 6:
        cut = 2 + (sum(ord(ch) for ch in salt) % 3)
        return (orig[:cut] + orig[cut + 1 :]).rstrip()
    return (orig + " ").rstrip() or "다"


def _transform(style: str, original: str, item_id: str) -> str | None:
    body = str(original or "").strip()
    if style in {"info_placement", "other", "consistency"}:
        return None
    if not body:
        return None
    if item_id == "W8":
        return _ensure_changed(body, _short_for_warning(body), item_id)
    if style == "explain_less":
        sug = _drop_last_sentence(body)
    elif style == "redundancy":
        sug = _drop_second_repeat(body)
    elif style == "abstract_expression":
        sug = _drop_fillers(body)
    elif style == "reader_question":
        sug = _drop_leading_clause(body)
    elif style == "action_clarity":
        sug = _clarity_tweak(body)
    else:
        sug = _drop_last_sentence(body)
    return _ensure_changed(body, sug, item_id)


def _plan_from_paras(paras: list[tuple[int, str]]) -> dict[str, dict[str, Any]]:
    text_paras = [(n, t) for n, t in paras if str(t or "").strip()] or [
        (1, "시험용 문단입니다.")
    ]
    ids = ["C1", "W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8"]
    span_ids = {"W2", "W5"}
    n = len(text_paras)
    idxs = _spread_indices(n, len(ids))
    plan: dict[str, dict[str, Any]] = {}
    for i, item_id in enumerate(ids):
        start = text_paras[idxs[i]]
        end = start
        if item_id in span_ids and n >= 2:
            chosen = None
            cur = idxs[i]
            for delta in (1, -1, 2, -2):
                nxt = cur + delta
                if 0 <= nxt < n and abs(text_paras[nxt][0] - start[0]) >= 1:
                    neighbor = text_paras[nxt]
                    if abs(neighbor[0] - start[0]) == 1:
                        chosen = neighbor
                        break
                    if chosen is None:
                        chosen = neighbor
            if chosen is not None:
                end = chosen
        plan[item_id] = {
            "range": _range_for_pair(start, end),
            "slot": i,
            "sample": start[1],
        }
    return plan


def _original_from_card_prompt(prompt: str) -> str:
    text = str(prompt or "")
    marker = "[대상 원문 - 수정안은 이 텍스트를 통째로 대체합니다]"
    idx = text.find(marker)
    if idx < 0:
        return ""
    rest = text[idx + len(marker) :].lstrip("\n")
    parts: list[str] = []
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        parts.append(line)
    return "\n".join(parts).strip()


def _item_block_fields(prompt: str) -> dict[str, str]:
    text = str(prompt or "")
    start = text.find("[대상 항목]")
    if start < 0:
        return {}
    end = text.find("[유형별 지침]", start)
    block = text[start:end] if end > start else text[start:]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


class UiFakeClaude(FakeClaude):
    """화면 시험용 응답. 실제 문단에 카드를 분산하고 이유·수정안을 서로 다르게 만든다."""

    def __init__(self, delay: float | None = None) -> None:
        super().__init__()
        self.delay = 0.7 if delay is None else max(0.0, float(delay))
        self._plan: dict[str, dict[str, Any]] = {}

    def generate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.prompts.append(prompt)
            self.kwargs.append(kwargs)
        text = str(prompt or "")
        if "인물·설정·시간·수치에 관한 사실" in text:
            return self._as_result(self._consistency(text))
        if "피드백 리포트를 JSON" in text:
            return self._as_result(self._report(text))
        if "첨삭 카드 1장" in text:
            if self.delay:
                time.sleep(self.delay)
            return self._as_result(self._card(text))
        return self._as_result(
            {
                "summary": "시험 모드 고정 응답입니다.",
                "rules": [],
                "scores": [],
                "strengths": [],
                "weaknesses": [],
                "consistency": [],
            }
        )

    def _ensure_plan(self, prompt: str) -> dict[str, dict[str, Any]]:
        paras = _paras_from_prompt(prompt)
        if paras and (not self._plan or len(paras) >= 3):
            self._plan = _plan_from_paras(paras)
        elif not self._plan:
            self._plan = _plan_from_paras(paras)
        return self._plan

    def _consistency(self, prompt: str) -> dict[str, Any]:
        plan = self._ensure_plan(prompt)
        spec = plan.get("C1") or {}
        sample = str(spec.get("sample") or "이 부분")
        rng = spec.get("range") or _range_for(1, sample)
        return {
            "facts": [],
            "issues": [
                {
                    "id": "C1",
                    "title": "호칭이 흔들릴 수 있어요",
                    "body": _reason_for("consistency", sample, int(spec.get("slot") or 0)),
                    "certainty": "maybe",
                    "impact": 3,
                    "perspectives": ["editor"],
                    "range": rng,
                }
            ],
        }

    def _report(self, prompt: str) -> dict[str, Any]:
        plan = self._ensure_plan(prompt)
        specs = [
            ("W1", "explain_less", "sentence", 5, "설명이 대화를 끊고 있어요"),
            ("W2", "action_clarity", "sentence", 5, "동작 순서가 겹쳐 보여요"),
            ("W3", "redundancy", "sentence", 4, "같은 정보가 반복돼요"),
            ("W4", "reader_question", "sentence", 3, "독자 의문이 남아요"),
            ("W5", "abstract_expression", "sentence", 3, "표현이 추상적이에요"),
            ("W6", "other", "sentence", 2, "문체가 살짝 흔들려요"),
            ("W7", "info_placement", "structure", 4, "정보가 이 자리에 없어요"),
            ("W8", "explain_less", "sentence", 1, "군더더기 한 줄이 있어요"),
            ("W9", "other", "none", 2, "취향의 문제예요"),
        ]
        items: list[dict[str, Any]] = []
        for spec in specs:
            item_id, style, fixable, impact, title = spec
            slot = plan.get(item_id, {}).get("slot", len(items))
            sample = str(plan.get(item_id, {}).get("sample") or "이 부분")
            rng = plan.get(item_id, {}).get("range")
            if rng is None:
                rng = _range_for(1, sample)
            body = (
                "고쳐야 하는 것은 아닙니다. 참고만 해 주세요."
                if item_id == "W9"
                else _reason_for(style, sample, int(slot) + 1)
            )
            items.append(
                {
                    "id": item_id,
                    "title": title,
                    "body": body,
                    "type": style,
                    "fixable": fixable,
                    "impact": impact,
                    "certainty": "sure",
                    "perspectives": ["editor"],
                    "range": rng,
                }
            )
        first = next(iter(plan.values()), {})
        s_range = first.get("range") or _range_for(1, "시험용 문단입니다.")
        return {
            "summary": "시험 모드 고정 리포트입니다. 실제 Claude를 호출하지 않았습니다.",
            "rules": [],
            "scores": [
                {"item": "몰입", "score": 4, "comment": "장면이 잘 이어집니다."},
                {"item": "호흡", "score": 3, "comment": "설명 구간이 조금 깁니다."},
                {"item": "캐릭터", "score": 4, "comment": "목소리가 분명합니다."},
            ],
            "strengths": [
                {
                    "title": "장면의 온도",
                    "body": "감각이 구체적으로 남아 있습니다.",
                    "range": s_range,
                }
            ],
            "weaknesses": items,
            "consistency": [],
        }

    def _card(self, prompt: str) -> dict[str, Any]:
        fields = _item_block_fields(prompt)
        item_id = str(fields.get("id") or "").split("/", 1)[0].strip()
        style = str(fields.get("type") or "").split()[0]
        original = _original_from_card_prompt(prompt)
        enabled = str(fields.get("suggestion_enabled") or "").strip().lower() == "true"
        slot = int((self._plan.get(item_id) or {}).get("slot") or 0)
        if item_id.startswith("C"):
            style = "consistency"
        reason = _reason_for(style or "other", original, slot)
        if not enabled or style in {"info_placement", "other", "consistency"}:
            suggestion = None
        else:
            suggestion = _transform(style or "explain_less", original, item_id)
        return {
            "target_id": item_id or "W1",
            "kind": "consistency" if item_id.startswith("C") else "style",
            "edit_plan": "지적된 문장만 손봅니다.",
            "reason": reason,
            "suggestion": suggestion,
            "added_facts": [],
            "removed_facts": [],
            "confidence": "medium",
        }
