"""회차 HTML/텍스트를 문단 배열로 바꾼다. 번호는 1부터."""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from typing import Any

import author_note_blocks

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
BLOCK_TAGS = frozenset({"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"})
OTHER_TAGS = frozenset({"table", "img", "figure"})
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
VOID_TAGS = frozenset({"br", "img", "hr", "meta", "input", "source", "col", "area", "wbr"})
SKIP_TAGS = frozenset({"script", "style"})


@dataclass
class Paragraph:
    number: int
    text: str
    type: str  # text | other | divider

    def as_dict(self) -> dict[str, Any]:
        return {"i": self.number, "text": self.text, "type": self.type}


@dataclass
class RangeHit:
    start_para: int
    end_para: int
    original_text: str
    start_quote: str
    end_quote: str


def normalize_invisible(text: str) -> str:
    """plain_text_from_content와 같이 NBSP·ZWSP를 정규화한다."""
    return (text or "").replace("\xa0", " ").replace("\u200b", "")


def normalize_text(text: str) -> str:
    raw = normalize_invisible(str(text or ""))
    chars: list[str] = []
    for ch in raw:
        chars.append(QUOTE_MAP.get(ch, ch))
    collapsed = re.sub(r"[ \t]+", " ", "".join(chars))
    return collapsed


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
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


def paragraphs_from_text(text: str) -> list[dict[str, Any]]:
    """빈 줄 기준. '#' 제목은 other, '***'와 '* * *'는 divider."""
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
            kind = "text"
        paragraphs.append(Paragraph(number=number, text=block.strip(), type=kind))
        number += 1
    return [p.as_dict() for p in paragraphs]


def to_paragraphs(items: list[dict[str, Any] | Paragraph]) -> list[Paragraph]:
    out: list[Paragraph] = []
    for item in items:
        if isinstance(item, Paragraph):
            out.append(item)
            continue
        number = int(item.get("i") or item.get("number") or 0)
        text = str(item.get("text") or "")
        kind = str(item.get("type") or "text")
        if kind == "body":
            kind = "text"
        out.append(Paragraph(number=number, text=text, type=kind))
    return out


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict[str, Any] = {"tag": "root", "attrs": {}, "children": []}
        self.stack = [self.root]
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        node = {"tag": tag, "attrs": {k.lower(): (v or "") for k, v in attrs}, "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        self.stack[-1]["children"].append(data)


def _class_names(node: dict[str, Any]) -> str:
    attrs = node.get("attrs") or {}
    return str(attrs.get("class") or "")


def _inner_text(node: dict[str, Any]) -> str:
    parts: list[str] = []
    for child in node.get("children") or []:
        if isinstance(child, str):
            parts.append(child)
        elif isinstance(child, dict):
            if child.get("tag") == "br":
                parts.append("\n")
            else:
                parts.append(_inner_text(child))
    text = "".join(parts)
    text = html_lib.unescape(text)
    text = normalize_invisible(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _norm_join(parts: list[str]) -> str:
    text = html_lib.unescape("".join(parts))
    text = normalize_invisible(text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text


def _split_leaf(node: dict[str, Any]) -> list[str]:
    """잎 블록을 br과 텍스트 노드 줄바꿈으로 나눈다."""
    buf: list[str] = []
    chunks: list[str] = []

    def flush() -> None:
        text = _norm_join(buf)
        buf.clear()
        if text:
            chunks.append(text)

    def push_text(raw: str) -> None:
        text = normalize_invisible(html_lib.unescape(raw or ""))
        pieces = re.split(r"\n+", text)
        for index, piece in enumerate(pieces):
            if index:
                flush()
            if piece:
                buf.append(piece)

    for child in node.get("children") or []:
        if isinstance(child, dict) and child.get("tag") == "br":
            flush()
            continue
        if isinstance(child, str):
            push_text(child)
            continue
        if isinstance(child, dict):
            if child.get("tag") in SKIP_TAGS:
                continue
            push_text(_inner_text(child))
    flush()
    return chunks


def _split_br_chunks(node: dict[str, Any]) -> list[str]:
    return _split_leaf(node)


def _is_structural(child: dict[str, Any]) -> bool:
    tag = child.get("tag")
    if tag in BLOCK_TAGS or tag in OTHER_TAGS:
        return True
    if tag == "div" and "manuscript-scene-break" in _class_names(child):
        return True
    return False


def _has_nested_blocks(node: dict[str, Any]) -> bool:
    for child in node.get("children") or []:
        if isinstance(child, dict) and _is_structural(child):
            return True
    return False


def _emit_leaf(node: dict[str, Any], acc: list[tuple[str, str]], kind: str) -> None:
    for chunk in _split_leaf(node):
        chunk_kind = kind
        if chunk.strip() in DIVIDER_TEXTS:
            chunk_kind = "divider"
        acc.append((chunk_kind, chunk))


def _walk_mixed(node: dict[str, Any], acc: list[tuple[str, str]]) -> None:
    children = node.get("children") or []
    if _has_nested_blocks(node):
        buf: list[Any] = []

        def flush_inlines() -> None:
            if not buf:
                return
            synthetic = {"tag": "span", "attrs": {}, "children": list(buf)}
            buf.clear()
            _emit_leaf(synthetic, acc, "text")

        for child in children:
            if isinstance(child, dict) and _is_structural(child):
                flush_inlines()
                _walk_blocks(child, acc)
            else:
                buf.append(child)
        flush_inlines()
        return
    _emit_leaf(node, acc, "text")


def _walk_blocks(node: dict[str, Any], acc: list[tuple[str, str]]) -> None:
    tag = str(node.get("tag") or "")
    if tag == "img" or tag in {"table", "figure"}:
        text = _inner_text(node) or (node.get("attrs") or {}).get("alt") or "(이미지)"
        if tag == "img" and not _inner_text(node):
            text = str((node.get("attrs") or {}).get("alt") or "").strip() or "(이미지)"
        acc.append(("other", text))
        return
    if tag == "div" and "manuscript-scene-break" in _class_names(node):
        acc.append(("divider", _inner_text(node) or "***"))
        return
    if tag in BLOCK_TAGS:
        if _has_nested_blocks(node):
            _walk_mixed(node, acc)
            return
        kind = "other" if tag in HEADING_TAGS else "text"
        _emit_leaf(node, acc, kind)
        return
    _walk_mixed(node, acc)


def _strip_html_to_plain(html: str) -> str:
    with_breaks = re.sub(r"(?i)<br\s*/?>", "\n", html or "")
    text = re.sub(r"(?s)<[^>]+>", "", with_breaks)
    return normalize_invisible(html_lib.unescape(text))


def _plain_chunks(text: str) -> list[tuple[str, str]]:
    """태그 없는 텍스트: 빈 줄로 나누고, 없으면 줄 단위."""
    source = normalize_invisible(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip():
        return []
    raw = (
        re.split(r"\n\s*\n+", source)
        if re.search(r"\n\s*\n", source)
        else source.split("\n")
    )
    out: list[tuple[str, str]] = []
    for chunk in raw:
        body = chunk.strip()
        if not body:
            continue
        if body in DIVIDER_TEXTS:
            kind = "divider"
        elif body.lstrip().startswith("#"):
            kind = "other"
        else:
            kind = "text"
        out.append((kind, body))
    return out


def paragraphs_from_html(html: str) -> list[dict[str, Any]]:
    """잎 블록·br·텍스트 줄바꿈을 문단으로, 구분선·표·이미지를 구분한다."""
    cleaned = author_note_blocks.strip_author_note_html(html or "")
    builder = _TreeBuilder()
    try:
        builder.feed(cleaned)
        builder.close()
    except Exception:
        acc = _plain_chunks(_strip_html_to_plain(cleaned))
        return [
            {"i": i, "text": text, "type": kind}
            for i, (kind, text) in enumerate(acc, start=1)
        ]
    acc: list[tuple[str, str]] = []
    _walk_blocks(builder.root, acc)
    if not acc:
        acc = _plain_chunks(_strip_html_to_plain(cleaned))
    out: list[dict[str, Any]] = []
    for i, (kind, text) in enumerate(acc, start=1):
        if kind == "text" and text.strip() in DIVIDER_TEXTS:
            kind = "divider"
        out.append({"i": i, "text": text, "type": kind})
    return out


def find_in_paragraph(paragraph: Paragraph, quote: str) -> int | None:
    needle = normalize_text(quote).strip()
    if not needle:
        return None
    hay, index_map = _normalize_with_map(paragraph.text)
    pos = hay.find(needle)
    if pos < 0 or pos >= len(index_map):
        return None
    return index_map[pos]


def _quote_end_original_index(paragraph: Paragraph, quote: str, start_orig: int) -> int:
    needle = normalize_text(quote).strip()
    hay, index_map = _normalize_with_map(paragraph.text)
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


def unique_paragraphs_with_quote(
    paragraphs: list[Paragraph], quote: str
) -> list[Paragraph]:
    hits: list[Paragraph] = []
    for para in paragraphs:
        if find_in_paragraph(para, quote) is not None:
            hits.append(para)
    return hits


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


def locate_quote_range(
    paragraphs: list[Paragraph],
    start_quote: str,
    end_quote: str,
) -> RangeHit | None:
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
        return None
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
        return None
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


def in_paragraph_outside(paragraphs: list[Paragraph], hit: RangeHit) -> tuple[str, str]:
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


def adjacent_outside_numbers(
    start_para: int, end_para: int, total: int, window: int = 3
) -> list[int]:
    before = [n for n in range(start_para - window, start_para) if n >= 1]
    after = [n for n in range(end_para + 1, end_para + 1 + window) if n <= total]
    return before + after


def format_paragraphs_text(paragraphs: list[Paragraph]) -> str:
    lines: list[str] = []
    for para in paragraphs:
        if para.type == "divider":
            lines.append("(장면 구분선)")
        elif para.type == "other" and para.text.lstrip().startswith("#"):
            lines.append(f"[P{para.number}] (제목) {para.text}")
        else:
            lines.append(f"[P{para.number}] {para.text}")
    return "\n\n".join(lines)


def source_hash(paragraphs: list[dict[str, Any] | Paragraph]) -> str:
    import hashlib

    parts: list[str] = []
    for para in to_paragraphs(paragraphs):
        parts.append(normalize_text(para.text).strip())
    blob = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
