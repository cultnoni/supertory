"""루브릭 필수 항목 고정·누락 보완."""

from __future__ import annotations

from typing import Any

SHORT_BASE_KEYS = (
    "opening",
    "pov",
    "scene_summary",
    "character",
    "motif",
    "implication",
    "ending",
    "economy",
    "style",
    "title",
)

LONG_INTERNAL_BASE = (
    "pov",
    "scene_summary",
    "character_interior",
    "implication",
    "style",
)

LONG_IN_WORK_BASE = (
    "continuity",
    "role",
    "character_consistency",
    "character_arc",
    "motif",
    "chapter_edges",
)

MISSING_VERDICT = "none"
MISSING_NOTE = "판정 없음"


def required_short_keys(*, contest_on: bool) -> list[str]:
    keys = list(SHORT_BASE_KEYS)
    if contest_on:
        keys.append("novelty")
    return keys


def required_long_keys(*, first: bool, last: bool, contest_on: bool) -> dict[str, list[str]]:
    internal = list(LONG_INTERNAL_BASE)
    in_work = list(LONG_IN_WORK_BASE)
    if first:
        internal = ["opening", *internal]
    if last:
        in_work = [*in_work, "ending"]
    if contest_on and first:
        internal = [*internal, "novelty"]
    return {"internal": internal, "in_work": in_work}


def _placeholder(key: str) -> dict[str, Any]:
    return {
        "key": key,
        "verdict": MISSING_VERDICT,
        "intentional": False,
        "note": MISSING_NOTE,
        "evidence": [],
    }


def missing_keys(items: list[dict[str, Any]], required: list[str]) -> list[str]:
    have = {str(item.get("key") or "") for item in items if isinstance(item, dict)}
    return [key for key in required if key not in have]


def fill_missing_items(
    items: list[dict[str, Any]],
    required: list[str],
    *,
    log: list[dict[str, str]] | None = None,
    bucket: str = "",
) -> list[dict[str, Any]]:
    """필수 키가 없으면 판정 없음으로 채우고 verification_log에 남긴다."""
    by_key: dict[str, dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key and key not in by_key:
            by_key[key] = item
    out: list[dict[str, Any]] = []
    for key in required:
        if key in by_key:
            out.append(by_key[key])
            continue
        out.append(_placeholder(key))
        if log is not None:
            log.append(
                {
                    "action": "rubric_missing_filled",
                    "target": f"{bucket}:{key}" if bucket else key,
                    "detail": MISSING_NOTE,
                }
            )
    return out


def ensure_short_rubric(
    rubric: dict[str, Any],
    *,
    contest_on: bool,
    log: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    required = required_short_keys(contest_on=contest_on)
    items = [item for item in (rubric.get("items") or []) if isinstance(item, dict)]
    absent = missing_keys(items, required)
    filled = fill_missing_items(items, required, log=log, bucket="items")
    strengths = list(rubric.get("strengths") or [])
    return {"items": filled, "strengths": strengths}, absent


def ensure_long_rubric(
    rubric: dict[str, Any],
    *,
    first: bool,
    last: bool,
    contest_on: bool,
    log: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    required = required_long_keys(first=first, last=last, contest_on=contest_on)
    internal = [item for item in (rubric.get("internal") or []) if isinstance(item, dict)]
    in_work = [item for item in (rubric.get("in_work") or []) if isinstance(item, dict)]
    absent = missing_keys(internal, required["internal"]) + missing_keys(in_work, required["in_work"])
    internal = fill_missing_items(internal, required["internal"], log=log, bucket="internal")
    in_work = fill_missing_items(in_work, required["in_work"], log=log, bucket="in_work")
    strengths = list(rubric.get("strengths") or [])
    return {
        "items": internal + in_work,
        "internal": internal,
        "in_work": in_work,
        "strengths": strengths,
    }, absent


def required_keys_prompt_line(*, short: bool = False, first: bool = False, last: bool = False, contest_on: bool = False) -> str:
    if short:
        keys = required_short_keys(contest_on=contest_on)
        return "필수 항목(모두 넣을 것): " + ", ".join(keys)
    parts = required_long_keys(first=first, last=last, contest_on=contest_on)
    return (
        "필수 internal: " + ", ".join(parts["internal"])
        + " / 필수 in_work: " + ", ".join(parts["in_work"])
        + " (빠진 항목 없이 모두 넣을 것)"
    )
