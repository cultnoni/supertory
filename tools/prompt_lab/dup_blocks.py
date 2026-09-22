"""Mechanical duplicate-block finder for report-stage lab.

Normalizes body paragraphs (strip whitespace and punctuation), then finds
consecutive runs of similar paragraphs (exact or SequenceMatcher ratio >= 0.9).
Not an AI check.
"""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from typing import Any

from manuscript import Paragraph

MIN_NORM_LEN = 8
MIN_RUN = 3
SIMILARITY = 0.9
BODY_TYPES = frozenset({"body", "text"})


def normalize_for_dup(text: str) -> str:
    """Remove whitespace and punctuation; keep letters/digits/Hangul."""
    chars: list[str] = []
    for ch in text or "":
        if ch.isspace():
            continue
        if unicodedata.category(ch).startswith("P"):
            continue
        chars.append(ch)
    return "".join(chars)


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY


def find_dup_blocks(paragraphs: list[Paragraph]) -> list[dict[str, list[int]]]:
    """Return [{"a": [start, end], "b": [start, end]}, ...] for runs of 3+ similar body paras."""
    candidates: list[tuple[int, str]] = []
    for para in paragraphs:
        if para.type not in BODY_TYPES:
            continue
        norm = normalize_for_dup(para.text)
        if len(norm) < MIN_NORM_LEN:
            continue
        candidates.append((para.number, norm))

    n = len(candidates)
    if n < MIN_RUN * 2:
        return []

    similar = [[False] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if _similar(candidates[i][1], candidates[j][1]):
                similar[i][j] = True

    blocks: list[dict[str, list[int]]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for offset in range(1, n):
        i = 0
        while i + offset < n:
            if similar[i][i + offset]:
                start = i
                while i + offset < n and similar[i][i + offset]:
                    i += 1
                length = i - start
                if length >= MIN_RUN:
                    a0 = candidates[start][0]
                    a1 = candidates[start + length - 1][0]
                    b0 = candidates[start + offset][0]
                    b1 = candidates[start + offset + length - 1][0]
                    key = (a0, a1, b0, b1)
                    if key not in seen:
                        seen.add(key)
                        blocks.append({"a": [a0, a1], "b": [b0, b1]})
            else:
                i += 1
    return blocks


def format_dup_findings(blocks: list[dict[str, list[int]]]) -> str:
    if not blocks:
        return "없음"
    lines: list[str] = []
    for block in blocks:
        a0, a1 = block["a"]
        b0, b1 = block["b"]
        lines.append(f"- 문단 중복: P{a0}~P{a1}과 P{b0}~P{b1}이 거의 같습니다.")
    return "\n".join(lines)


def verify_expected(
    paragraphs_by_key: dict[str, list[Paragraph]],
) -> dict[str, Any]:
    """Sanity check: ep2/ep2x → 0 blocks, ep2dup → at least one."""
    counts: dict[str, int] = {}
    unexpected: list[str] = []
    details: dict[str, list[dict[str, list[int]]]] = {}
    for key, paras in paragraphs_by_key.items():
        blocks = find_dup_blocks(paras)
        details[key] = blocks
        counts[key] = len(blocks)
        if key in {"ep2", "ep2x"} and blocks:
            unexpected.append(f"{key}: {len(blocks)}개 (기대 0)")
        if key == "ep2dup" and not blocks:
            unexpected.append("ep2dup: 0개 (기대 1개 이상)")
    return {
        "counts": counts,
        "blocks": details,
        "ok": not unexpected,
        "unexpected": unexpected,
    }
