"""기계적 문단 중복 블록 찾기. AI를 쓰지 않는다."""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from typing import Any

from feedback_pipeline.paragraphs import Paragraph, to_paragraphs

MIN_NORM_LEN = 8
MIN_RUN = 3
SIMILARITY = 0.9
BODY_TYPES = frozenset({"body", "text"})


def normalize_for_dup(text: str) -> str:
    """공백·문장부호를 빼고 글자만 남긴다."""
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


def find_dup_blocks(paragraphs: list[Any]) -> list[dict[str, list[int]]]:
    """연속 3문단 이상이 비슷하면 {"a": [start, end], "b": [start, end]}."""
    paras = to_paragraphs(paragraphs) if paragraphs and not isinstance(paragraphs[0], Paragraph) else list(paragraphs)
    candidates: list[tuple[int, str]] = []
    for para in paras:
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


def dup_reason(block: dict[str, list[int]]) -> str:
    a0, a1 = block["a"]
    b0, b1 = block["b"]
    return f"문단 P{a0}~P{a1}과 P{b0}~P{b1}이 거의 같습니다. 한 버전만 남기는 편이 좋습니다."
