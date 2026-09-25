"""작가에게 보여주는 실행 실패 문구. 내부 오류 문장은 저장하지 않는다."""

from __future__ import annotations

PUBLIC_RUN_FAILURE = "지금은 토리가 원고를 읽을 수 없어요. 잠시 후 다시 시도해 주세요."


def public_failure(params: dict | None, *, cards: bool = False) -> dict:
    cleaned = dict(params or {})
    cleaned.pop("error", None)
    cleaned["public_error"] = PUBLIC_RUN_FAILURE
    if cards:
        cleaned["cards_failed"] = True
    return cleaned
