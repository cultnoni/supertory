"""문학 호출의 공통 앞부분. 시스템 → 원고+설정집(캐시) → 단계 지시 순서로 고정한다."""

from __future__ import annotations

import json
from typing import Any

from feedback_pipeline.claude_client import estimate_cost_usd


def cache_prefix(manuscript: str, settings: str) -> str:
    body = str(manuscript or "").strip() or "(원고 없음)"
    notes = str(settings or "").strip() or "(없음)"
    return f"원고:\n{body}\n\n설정집:\n{notes}"


def build_cached_system(prefix: str, common_system: str) -> list[dict[str, Any]]:
    """캐시 접두: [공통 시스템] → [원고+설정집 + cache_control]."""
    common = str(common_system or "").rstrip() or "(지시 없음)"
    body = str(prefix or "").strip() or "(앞부분 없음)"
    return [
        {"type": "text", "text": common},
        {
            "type": "text",
            "text": body,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def with_schema_instruction(prompt: str, schema: dict[str, Any] | None) -> str:
    """단계별 JSON 스키마는 캐시 구간 뒤(유저 메시지)에만 둔다.

    output_config/structured outputs는 스키마를 접두에 넣어 단계마다 캐시 쓰기를
    다시 만들므로 문학 파이프라인에서는 쓰지 않는다.
    """
    text = str(prompt or "").rstrip() or "(지시 없음)"
    if not isinstance(schema, dict) or not schema:
        return text
    return text + "\n\n출력은 다음 JSON 스키마만 따른다.\n" + json.dumps(schema, ensure_ascii=False)


def settings_for_stage(frozen: str, live: str) -> str:
    """캐시에 넣은 설정집과 같으면 중복을 피하고, 문체 갱신 뒤에만 최신을 붙인다."""
    cached = str(frozen or "").strip()
    current = str(live or "").strip()
    if not current or current == cached:
        return "설정집은 메시지 앞부분에 있다."
    return "메시지 앞부분의 설정집에 더해, 방금 갱신된 설정집:\n" + current


def estimate_shared_prefix_cost(prefix_tokens: int, cached_calls: int) -> dict[str, float]:
    """같은 앞부분을 cached_calls번 쓸 때의 캐시 전후 비용."""
    calls = max(1, int(cached_calls))
    tokens = max(0, int(prefix_tokens))
    without = estimate_cost_usd("claude-sonnet-5", tokens * calls, 0)
    with_cache = estimate_cost_usd(
        "claude-sonnet-5",
        0,
        0,
        cache_write_tokens=tokens,
        cache_read_tokens=tokens * (calls - 1),
    )
    return {
        "prefix_tokens": float(tokens),
        "cached_calls": float(calls),
        "without_cache_usd": round(without, 4),
        "with_cache_usd": round(with_cache, 4),
        "saved_usd": round(without - with_cache, 4),
    }
