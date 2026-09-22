"""Manuscript parsing and quote-range lookup for the prompt lab.

No app imports. Paragraph numbers are 1-based over the whole file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DIVIDER_TEXTS = frozenset({"***", "* * *"})
QUOTE_MAP = {
    "\u00a0": " ",
    "\u202f": " ",
    "\u3000": " ",
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u300c": '"',
    "\u300d": '"',
    "\u300e": '"',
    "\u300f": '"',
}


@dataclass
class Paragraph:
    number: int
    text: str
    type: str  # "body" | "other" | "divider"


@dataclass
class RangeHit:
    start_para: int
    end_para: int
    original_text: str
    start_quote: str
    end_quote: str


@dataclass
class LocateError(Exception):
    case_id: str
    quote_kind: str
    quote: str
    manuscript: str

    def __str__(self) -> str:
        return (
            f"[{self.case_id}] {self.quote_kind}를 찾지 못함 "
            f"({self.manuscript}): {self.quote!r}"
        )


def normalize_text(text: str) -> str:
    raw = str(text or "")
    chars: list[str] = []
    for ch in raw:
        chars.append(QUOTE_MAP.get(ch, ch))
    collapsed = re.sub(r"[ \t]+", " ", "".join(chars))
    return collapsed


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return (normalized_string, map from norm_index -> original index)."""
    tmp: list[str] = []
    tmp_map: list[int] = []
    for i, ch in enumerate(text or ""):
        mapped = QUOTE_MAP.get(ch, ch)
        tmp.append(mapped)
        tmp_map.append(i)
    out: list[str] = []
    out_map: list[int] = []
    prev_space = False
    for ch, orig_i in zip(tmp, tmp_map):
        if ch in " \t":
            if prev_space:
                continue
            out.append(" ")
            out_map.append(orig_i)
            prev_space = True
            continue
        prev_space = False
        out.append(ch)
        out_map.append(orig_i)
    return "".join(out), out_map


def parse_paragraphs(text: str) -> list[Paragraph]:
    source = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    chunks = re.split(r"\n\s*\n+", source)
    paragraphs: list[Paragraph] = []
    number = 1
    for chunk in chunks:
        block = chunk.strip("\n")
        stripped = block.strip()
        if not stripped:
            continue
        first_line = stripped.split("\n", 1)[0].strip()
        if first_line.startswith("#"):
            kind = "other"
        elif stripped in DIVIDER_TEXTS:
            kind = "divider"
        else:
            kind = "body"
        paragraphs.append(Paragraph(number=number, text=block.strip(), type=kind))
        number += 1
    return paragraphs


def find_in_paragraph(paragraph: Paragraph, quote: str) -> int | None:
    """Original-text index of quote in paragraph, or None."""
    needle = normalize_text(quote).strip()
    if not needle:
        return None
    hay, index_map = _normalize_with_map(paragraph.text)
    pos = hay.find(needle)
    if pos < 0:
        return None
    if pos >= len(index_map):
        return None
    return index_map[pos]


def _quote_end_original_index(paragraph: Paragraph, quote: str, start_orig: int) -> int:
    needle = normalize_text(quote).strip()
    hay, index_map = _normalize_with_map(paragraph.text)
    # Map original start index back onto the normalized string.
    norm_start = 0
    for ni, oi in enumerate(index_map):
        if oi >= start_orig:
            norm_start = ni
            break
    pos = hay.find(needle, norm_start)
    if pos < 0:
        pos = hay.find(needle)
    if pos < 0:
        return start_orig + len(quote)
    end_norm = pos + len(needle) - 1
    if end_norm >= len(index_map):
        return len(paragraph.text)
    return index_map[end_norm] + 1


def locate_quote_range(
    paragraphs: list[Paragraph],
    start_quote: str,
    end_quote: str,
    *,
    case_id: str = "",
    manuscript: str = "",
) -> RangeHit:
    start_q = str(start_quote or "")
    end_q = str(end_quote or "")
    start_para: Paragraph | None = None
    start_at: int | None = None
    for para in paragraphs:
        at = find_in_paragraph(para, start_q)
        if at is not None:
            start_para = para
            start_at = at
            break
    if start_para is None or start_at is None:
        raise LocateError(case_id, "start_quote", start_q, manuscript)

    end_para: Paragraph | None = None
    end_at: int | None = None
    for para in paragraphs:
        if para.number < start_para.number:
            continue
        hay, index_map = _normalize_with_map(para.text)
        needle = normalize_text(end_q).strip()
        if not needle:
            continue
        search_from = 0
        if para.number == start_para.number:
            for ni, oi in enumerate(index_map):
                if oi >= start_at:
                    search_from = ni
                    break
        pos = hay.find(needle, search_from)
        if pos < 0:
            continue
        orig = index_map[pos] if pos < len(index_map) else 0
        end_para = para
        end_at = orig
        break
    if end_para is None or end_at is None:
        raise LocateError(case_id, "end_quote", end_q, manuscript)

    original = slice_original(
        paragraphs,
        start_para.number,
        end_para.number,
        start_q,
        end_q,
        start_at=start_at,
        end_at=end_at,
    )
    return RangeHit(
        start_para=start_para.number,
        end_para=end_para.number,
        original_text=original,
        start_quote=start_q,
        end_quote=end_q,
    )


def slice_original(
    paragraphs: list[Paragraph],
    start_para: int,
    end_para: int,
    start_quote: str,
    end_quote: str,
    *,
    start_at: int | None = None,
    end_at: int | None = None,
) -> str:
    by_n = {p.number: p for p in paragraphs}
    first = by_n.get(start_para)
    last = by_n.get(end_para)
    if first is None or last is None:
        return ""
    if start_at is None:
        start_at = find_in_paragraph(first, start_quote) or 0
    if end_at is None:
        end_at = find_in_paragraph(last, end_quote)
        if end_at is None:
            end_at = 0
    last_end = _quote_end_original_index(last, end_quote, end_at)
    if start_para == end_para:
        return first.text[start_at:last_end]
    parts: list[str] = [first.text[start_at:]]
    for para in paragraphs:
        if start_para < para.number < end_para:
            parts.append(para.text)
    parts.append(last.text[:last_end])
    return "\n".join(parts)


def in_paragraph_outside(paragraphs: list[Paragraph], hit: RangeHit) -> tuple[str, str]:
    """Text in the start/end paragraphs that sits outside the quoted range."""
    by_n = {p.number: p for p in paragraphs}
    first = by_n.get(hit.start_para)
    last = by_n.get(hit.end_para)
    if first is None or last is None:
        return "(없음)", "(없음)"
    start_at = find_in_paragraph(first, hit.start_quote)
    if start_at is None:
        start_at = 0
    end_at = find_in_paragraph(last, hit.end_quote)
    if end_at is None:
        end_at = 0
    last_end = _quote_end_original_index(last, hit.end_quote, end_at)
    before = first.text[:start_at].strip()
    after = last.text[last_end:].strip()
    return (before or "(없음)", after or "(없음)")


def format_para_block(paragraphs: list[Paragraph], numbers: list[int]) -> str:
    by_n = {p.number: p for p in paragraphs}
    lines: list[str] = []
    for n in numbers:
        para = by_n.get(n)
        if para is None:
            continue
        lines.append(f"[P{n}] {para.text}")
    return "\n".join(lines) if lines else "(없음)"


def context_numbers(start_para: int, end_para: int, total: int) -> list[int]:
    before = [n for n in range(start_para - 2, start_para) if n >= 1]
    after = [n for n in range(end_para + 1, end_para + 3) if n <= total]
    return before + after


def adjacent_outside_numbers(start_para: int, end_para: int, total: int, window: int = 3) -> list[int]:
    before = [n for n in range(start_para - window, start_para) if n >= 1]
    after = [n for n in range(end_para + 1, end_para + 1 + window) if n <= total]
    return before + after


def format_project_terms(project: dict[str, Any] | None) -> str:
    if not project:
        return "(없음)"
    lines: list[str] = []
    title = str(project.get("title") or "").strip()
    genre = str(project.get("genre") or "").strip()
    if title:
        lines.append(f"작품: {title}")
    if genre:
        lines.append(f"장르: {genre}")
    for person in project.get("characters") or []:
        if not isinstance(person, dict):
            continue
        name = str(person.get("name") or "").strip()
        note = str(person.get("note") or "").strip()
        if name and note:
            lines.append(f"인물: {name} — {note}")
        elif name:
            lines.append(f"인물: {name}")
    for fact in project.get("tracked_facts") or []:
        text = str(fact or "").strip()
        if text:
            lines.append(f"사실: {text}")
    for term in project.get("terms") or []:
        text = str(term or "").strip()
        if text:
            lines.append(f"용어: {text}")
    for note in project.get("term_notes") or []:
        text = str(note or "").strip()
        if text:
            lines.append(f"용어 주석: {text}")
    return "\n".join(lines) if lines else "(없음)"
